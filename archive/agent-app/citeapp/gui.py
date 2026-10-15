"""gui.py — the app-mode desktop window (tkinter only, A2/A12).

A two-pane window with a menu bar (A12), a read-only but selectable document pane
on the left (A9/A11), and an interaction pane on the right whose top is a status
strip wired to the backend (A14). It loads a report by File-open dialog (every
platform) or Windows drag-and-drop (A21), or starts an ephemeral scratch session
from pasted text (A36/A37); after Insert it shows the cited document and
``references.md`` in dedicated result tabs (A38). It runs the backend ``sync`` on
a worker thread with live progress (A22/A26), and edits app settings through a
Settings dialog with an obfuscated token (A18, A24).

Stage A2 adds the first two scoped LLM calls (``DESIGN-app-mode.md`` §2): the
author selects prose and the right pane proposes citable sentences (call 1,
verbatim-checked A20), offers a pure-GUI keep/drop checklist (A8), then on
confirmation generates claims (call 2), scaffolds a session, writes
``claims.json`` and runs the existing search to produce ``review.md`` plus the
structured candidates it lists (A32). Stage A3 adds candidate evaluation (call 3,
``tasks.evaluate_review``): the recommendations view pre-ticks the model's chosen
candidates, offers Open-in-Zotero and DOI links (A13), and an Insert button that
calls ``insert_session(select=...)`` then reloads the report to show the
inserted citations.

Threading (A26): the Tk event loop owns every widget. Backend and LLM work run on
a single worker thread that only puts messages on a queue; a periodic ``after``
poll on the UI thread drains the queue and is the only place widgets are touched
(the pattern validated in spike 07).
"""

from __future__ import annotations

import datetime
import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, font as tkfont, messagebox, ttk

from refcite import (
    check_connection,
    insert_session,
    load_config,
    load_records,
    records_by_id,
    sync,
    zotero_select_uri,
)

from . import connection, dragdrop, llm, orchestrator, tasks
from .metrics import MetricsLog
from .settings import (
    PROVIDER_PRESETS,
    TOKEN_ENV_VAR,
    Settings,
    detect_provider,
    load_settings,
    save_settings,
)

# Eight pale highlight colours, assigned cyclically to confirmed sentences so
# adjacent claims are visually distinct in the document pane (A30). The matching
# statement row in the right pane uses the same colour as its label background.
CLAIM_COLOURS = (
    "#fff3b0", "#cde8c5", "#bcd5f0", "#f5c6d0", "#e2d1f0",
    "#ffd9b0", "#c6e9e6", "#e9e2b8",
)

# Call-to-action button styling: the primary step in each phase (Propose, Find
# citations) is rendered as a larger green button with white text to lead the eye
# through the normal flow, the way a website's main action button would.
CTA_BG = "#2e8b57"
CTA_ACTIVE_BG = "#35a066"
CTA_FG = "#ffffff"


class CiteApp:
    """The main application window and its (single) backend worker."""

    def __init__(self, root: tk.Tk):
        self.root = root
        self.settings: Settings = load_settings()
        self.cfg = None                      # set once a report is loaded
        self.report_path: Path | None = None
        # True while the loaded document is an ephemeral paste session (A37) and
        # so has no author-owned destination; results are read from the tabs (A38).
        self.is_scratch_session = False
        self.metrics: MetricsLog | None = None
        self.dev_mode = tk.BooleanVar(value=False)
        # Whether Windows file drag-and-drop was wired up (A21); set by ``run``
        # after construction so the instructions can be honest per platform.
        self.drag_drop_available = False

        # Stage A2 workflow state (statement selection + claim generation).
        self.phase = "idle"                  # idle | statements | candidates
        self.proposed: list[str] = []        # verbatim sentences from LLM call 1
        self.statement_vars: list = []       # keep/drop BooleanVars, parallel to it
        self.session_dir: Path | None = None
        self.last_summary: dict | None = None
        self._workflow_buttons: list = []    # disabled while a worker is busy (A26)
        # Candidate checkbox vars in the recommendations view, keyed by code (A32).
        self.candidate_vars: dict = {}
        self._rec_by_id: dict = {}           # cache records for Open-in-Zotero (A13)
        # Labels whose wraplength tracks the panel width: (label, reserved_px).
        self._wrap_labels: list = []

        # Worker plumbing: the worker posts (kind, payload) tuples here; the UI
        # thread drains them in ``_poll`` and is the only thread to touch Tk.
        self._queue: "queue.Queue[tuple]" = queue.Queue()
        self._busy = False                   # one backend op at a time (A26)

        self._build_menu()
        self._build_body()
        self._show_placeholder()
        self._show_idle_phase()
        self._set_status("No report loaded — drag a Markdown file onto the window, "
                         "use File → Open, or File → New from paste.")
        self._refresh_action_state()
        self.root.after(50, self._poll)

    # --- window construction ---------------------------------------------

    def _build_menu(self) -> None:
        menubar = tk.Menu(self.root)

        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label="Open report\u2026", command=self._open_dialog,
                              accelerator="Ctrl+O")
        file_menu.add_command(label="New from paste\u2026",
                              command=self._new_from_paste, accelerator="Ctrl+N")
        file_menu.add_separator()
        file_menu.add_command(label="Start over (clear selection)",
                              command=self._reset_workflow)
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self.root.destroy)
        menubar.add_cascade(label="File", menu=file_menu)

        settings_menu = tk.Menu(menubar, tearoff=0)
        settings_menu.add_command(label="Settings\u2026", command=self._open_settings)
        menubar.add_cascade(label="Settings", menu=settings_menu)

        help_menu = tk.Menu(menubar, tearoff=0)
        help_menu.add_checkbutton(label="Developer: show raw JSON",
                                  variable=self.dev_mode)
        help_menu.add_separator()
        help_menu.add_command(label="About", command=self._show_about)
        menubar.add_cascade(label="Help", menu=help_menu)

        self.root.config(menu=menubar)
        self.root.bind("<Control-o>", lambda _e: self._open_dialog())
        self.root.bind("<Control-n>", lambda _e: self._new_from_paste())

    def _build_body(self) -> None:
        paned = ttk.PanedWindow(self.root, orient="horizontal")
        paned.pack(fill="both", expand=True)

        # Left: a tabbed notebook. The first tab is the read-only document pane
        # (A9); it stays selectable (A11) so the author can highlight prose to
        # send to LLM call 1. The second tab shows the generated review.md so the
        # author can read candidates side-by-side without leaving the app. Edits
        # are blocked by a key filter rather than ``state="disabled"`` (which
        # would also block mouse selection).
        self.left_nb = ttk.Notebook(paned)

        doc_tab = ttk.Frame(self.left_nb)
        self.doc_text = tk.Text(doc_tab, wrap="word", undo=False,
                                font=("TkFixedFont",), padx=8, pady=8)
        doc_scroll = ttk.Scrollbar(doc_tab, orient="vertical",
                                   command=self.doc_text.yview)
        self.doc_text.configure(yscrollcommand=doc_scroll.set)
        self._make_readonly(self.doc_text)
        for i, colour in enumerate(CLAIM_COLOURS):
            self.doc_text.tag_configure(f"claim{i}", background=colour)
        doc_scroll.pack(side="right", fill="y")
        self.doc_text.pack(side="left", fill="both", expand=True)
        self.left_nb.add(doc_tab, text="Document")

        review_tab = ttk.Frame(self.left_nb)
        self.review_text = tk.Text(review_tab, wrap="word", undo=False,
                                   font=("TkFixedFont",), padx=8, pady=8)
        review_scroll = ttk.Scrollbar(review_tab, orient="vertical",
                                      command=self.review_text.yview)
        self.review_text.configure(yscrollcommand=review_scroll.set)
        self._make_readonly(self.review_text)
        review_scroll.pack(side="right", fill="y")
        self.review_text.pack(side="left", fill="both", expand=True)
        self.left_nb.add(review_tab, text="review.md")
        self._review_tab = review_tab
        self._set_review_placeholder()

        # The two result tabs (A38) hold the cited document and references.md
        # after Insert. They exist for both input modes but stay hidden until
        # there is something to show; for a pasted session (A37) they are the
        # only way to retrieve the results, so the author copies from them.
        cited_tab = ttk.Frame(self.left_nb)
        self.cited_text = tk.Text(cited_tab, wrap="word", undo=False,
                                  font=("TkFixedFont",), padx=8, pady=8)
        cited_scroll = ttk.Scrollbar(cited_tab, orient="vertical",
                                     command=self.cited_text.yview)
        self.cited_text.configure(yscrollcommand=cited_scroll.set)
        self._make_readonly(self.cited_text)
        cited_scroll.pack(side="right", fill="y")
        self.cited_text.pack(side="left", fill="both", expand=True)
        self.left_nb.add(cited_tab, text="Cited text")
        self._cited_tab = cited_tab

        refs_tab = ttk.Frame(self.left_nb)
        self.refs_text = tk.Text(refs_tab, wrap="word", undo=False,
                                 font=("TkFixedFont",), padx=8, pady=8)
        refs_scroll = ttk.Scrollbar(refs_tab, orient="vertical",
                                    command=self.refs_text.yview)
        self.refs_text.configure(yscrollcommand=refs_scroll.set)
        self._make_readonly(self.refs_text)
        refs_scroll.pack(side="right", fill="y")
        self.refs_text.pack(side="left", fill="both", expand=True)
        self.left_nb.add(refs_tab, text="References")
        self._refs_tab = refs_tab
        self._reset_result_tabs()

        paned.add(self.left_nb, weight=3)

        # Right: the interaction pane. The status strip is always present; the
        # workflow area below it swaps content per phase (idle guide → proposed
        # statements → candidate review).
        right = ttk.Frame(paned)
        self._build_status_strip(right)

        self.workflow = ttk.LabelFrame(right, text="Citations")
        self.workflow.pack(fill="both", expand=True, padx=10, pady=(6, 4))

        # The action bar sits *below* the (scrollable) Citations panel so the
        # primary buttons stay visible even when the statement list is long
        # enough to need a scrollbar.
        self.action_bar = ttk.Frame(right)
        self.action_bar.pack(fill="x", padx=10, pady=(0, 10))
        paned.add(right, weight=2)

    def _build_status_strip(self, parent) -> None:
        strip = ttk.LabelFrame(parent, text="Cache & Zotero")
        strip.pack(fill="x", padx=10, pady=(10, 4))

        self.cache_label = ttk.Label(strip, text="Cache: \u2014", anchor="w")
        self.cache_label.pack(fill="x", padx=8, pady=(6, 0))
        self.zotero_label = ttk.Label(strip, text="Zotero: \u2014", anchor="w")
        self.zotero_label.pack(fill="x", padx=8, pady=(0, 4))

        row = ttk.Frame(strip)
        row.pack(fill="x", padx=8, pady=(0, 6))
        self.sync_button = ttk.Button(row, text="Sync now", command=self._sync_now)
        self.sync_button.pack(side="left")
        self.refresh_button = ttk.Button(row, text="Refresh status",
                                         command=self._refresh_status_async)
        self.refresh_button.pack(side="left", padx=(6, 0))

        self.progress = ttk.Progressbar(strip, mode="determinate")
        self.progress.pack(fill="x", padx=8, pady=(0, 4))
        self.progress_label = ttk.Label(strip, text="", anchor="w", foreground="#666")
        self.progress_label.pack(fill="x", padx=8, pady=(0, 6))

        # Bottom-of-window status bar.
        self.status_bar = ttk.Label(self.root, anchor="w", relief="sunken")
        self.status_bar.pack(side="bottom", fill="x")

    # --- report loading ---------------------------------------------------

    def _open_dialog(self) -> None:
        path = filedialog.askopenfilename(
            title="Open report",
            filetypes=[("Markdown", "*.md"), ("Text", "*.txt"), ("All files", "*.*")],
        )
        if path:
            self.load_report(Path(path))

    def _new_from_paste(self) -> None:
        """Start an ephemeral scratch session from pasted text (A36/A37)."""
        PasteDialog(self.root, on_submit=self._load_pasted_text)

    def _load_pasted_text(self, text: str) -> None:
        """Write pasted prose to an ephemeral scratch document and load it (A37)."""
        document = orchestrator.create_scratch_document(text)
        self.load_report(document, scratch=True)

    def _on_drop(self, paths: list[str]) -> None:
        # This runs inside the Win32 WM_DROPFILES handler, which Windows invokes
        # *re-entrantly from within Tk's own message pump*. Calling ANY Tk/Tcl
        # function here (even ``root.after``) re-enters the interpreter from
        # inside a Tcl callback and corrupts its thread state, crashing with a
        # fatal "PyEval_RestoreThread ... the GIL is released" error. So touch
        # only the plain thread-safe queue; the ``_poll`` loop already running
        # on the UI thread drains it and loads the report safely.
        if paths:
            self._queue.put(("dropped", list(paths)))

    def load_report(self, path: Path, *, scratch: bool = False) -> None:
        """Load a report read-only; project root = its folder (A10/A37).

        ``scratch=True`` marks a pasted session whose folder is the ephemeral
        scratch root (A37); the results are then read from the result tabs (A38)
        rather than from an author-owned file.
        """
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            messagebox.showerror("Could not open report", f"{path}\n\n{exc}")
            return

        self.report_path = path
        self.is_scratch_session = scratch
        # Project root = the folder containing the report (A10); for a pasted
        # session that is the ephemeral scratch folder (A37). The GUI's
        # search-backend choice rides in as load_config overrides (A35).
        self.cfg = load_config(repo_root=path.parent,
                               overrides=self.settings.config_overrides())

        self.doc_text.delete("1.0", "end")
        self.doc_text.insert("1.0", text)
        self._clear_highlights()
        self._set_review_placeholder()
        self._reset_result_tabs()
        self.left_nb.select(0)

        # A fresh report restarts the workflow at statement selection.
        self.proposed = []
        self.statement_vars = []
        self.session_dir = None
        self.last_summary = None

        # Open the per-session metrics log now, before any LLM call (Stage A1
        # research requirement), so no comparison data is lost later.
        report_label = "paste" if scratch else path.stem
        session_id = f"{datetime.datetime.now():%Y%m%dT%H%M%S}_{report_label}"
        self.metrics = MetricsLog(session_id)
        self.metrics.session_started(
            model=self.settings.model,
            backend=self.settings.search_backend,
            report=("(pasted text)" if scratch else str(path)),
        )

        if scratch:
            self.root.title("Cite-Assist \u2014 pasted text")
            self._set_status("Loaded pasted text (ephemeral scratch session "
                             f"at {path.parent}).")
        else:
            self.root.title(f"Cite-Assist \u2014 {path.name}")
            self._set_status(f"Loaded {path}  (project root: {path.parent})")
        self._show_idle_phase()
        self._refresh_status_async()
        self._refresh_action_state()

    # --- backend worker ---------------------------------------------------

    def _run_worker(self, func, *, kind: str) -> None:
        """Run ``func`` on the single worker thread, posting its result/errors."""
        if self._busy:
            return
        self._busy = True
        self._refresh_action_state()

        def target() -> None:
            try:
                result = func()
                self._queue.put((kind, result))
            except Exception as exc:  # surface to the UI, never crash the worker
                self._queue.put(("error", (kind, exc)))

        threading.Thread(target=target, daemon=True).start()

    def _on_progress(self, label: str, current: int, total: int) -> None:
        # A22 callback, fired from the worker thread — only enqueue here.
        self._queue.put(("progress", (label, current, total)))

    def _poll(self) -> None:
        try:
            while True:
                kind, payload = self._queue.get_nowait()
                self._handle_message(kind, payload)
        except queue.Empty:
            pass
        self.root.after(50, self._poll)

    def _handle_message(self, kind: str, payload) -> None:
        if kind == "dropped":
            # A file was dropped onto the window (A21); load it now that we are
            # safely back on the UI thread (see _on_drop for why this is queued).
            self.load_report(Path(payload[0]))
        elif kind == "progress":
            label, current, total = payload
            self.progress.configure(maximum=max(total, 1), value=current)
            pct = int(current * 100 / total) if total else 0
            self.progress_label.configure(text=f"{label}: {current}/{total} ({pct}%)")
        elif kind == "status":
            self._busy = False
            self._apply_status(payload)
            self._refresh_action_state()
        elif kind == "sync_done":
            self._busy = False
            self.progress.configure(value=0)
            self.progress_label.configure(text="")
            n = payload.get("total", payload.get("records", "?"))
            self._set_status(f"Sync complete ({payload.get('status', 'ok')}); "
                             f"{n} records cached.")
            if self.metrics:
                self.metrics.log("sync", **{k: v for k, v in payload.items()
                                            if isinstance(v, (int, str))})
            self._refresh_status_async()
        elif kind == "proposed":
            self._busy = False
            self.proposed = list(payload.sentences)
            if self.metrics:
                self.metrics.log("proposed", kept=len(payload.sentences),
                                 non_verbatim=len(payload.non_verbatim))
            if not self.proposed:
                self._set_status("The model proposed no citable sentences. Try a "
                                 "different passage.")
                self._show_idle_phase()
            else:
                extra = (f" ({len(payload.non_verbatim)} non-verbatim dropped)"
                         if payload.non_verbatim else "")
                self._set_status(f"Proposed {len(self.proposed)} sentence(s){extra}. "
                                 "Tick the ones to cite, then Find citations.")
                self._show_statements_phase()
            self._refresh_action_state()
        elif kind == "found":
            self._busy = False
            self.progress.configure(value=0)
            self.progress_label.configure(text="")
            self.session_dir = payload.get("session_dir")
            self.last_summary = payload
            summary = payload.get("summary", {})
            if self.metrics:
                self.metrics.log("claims_searched",
                                 claims=summary.get("claims", 0),
                                 searched=summary.get("searched", 0),
                                 recommended=len(payload.get("recommendations", {})),
                                 session=getattr(self.session_dir, "name", ""))
            eval_error = payload.get("eval_error")
            if eval_error:
                self._set_status(
                    f"Searched {summary.get('searched', 0)}/"
                    f"{summary.get('claims', 0)} claim(s); review.md written. "
                    f"Evaluation step failed ({eval_error}); tick candidates "
                    "yourself.")
            else:
                self._set_status(
                    f"Searched {summary.get('searched', 0)}/"
                    f"{summary.get('claims', 0)} claim(s); recommendations ready "
                    f"in review.md at {summary.get('review', '?')}.")
            self._show_candidates_phase()
            self._refresh_action_state()
        elif kind == "inserted":
            self._busy = False
            self.progress.configure(value=0)
            self.progress_label.configure(text="")
            applied = payload.get("applied", [])
            added = payload.get("references_added", 0)
            skipped = payload.get("skipped", [])
            if self.metrics:
                self.metrics.log("inserted", applied=len(applied),
                                 references_added=added, skipped=len(skipped))
            self._set_status(
                f"Inserted {len(applied)} citation(s); {added} reference(s) "
                "added; session archived to done/.")
            # Reload the report so the freshly inserted citations are visible in
            # the document tab; this also resets the workflow to idle (A28).
            # Preserve the scratch flag so a pasted session stays a paste session.
            if self.report_path:
                self.load_report(self.report_path, scratch=self.is_scratch_session)
            # Reveal the Cited-text and References result tabs (A38); for a
            # pasted session these are the only way to retrieve the results.
            self._show_result_tabs()
            msg = (f"Inserted {len(applied)} citation(s).\n"
                   f"{added} reference(s) added to references.md.")
            if self.is_scratch_session:
                msg += ("\n\nThis was a pasted (scratch) session: copy the results "
                        "from the Cited text and References tabs \u2014 the scratch "
                        "folder is temporary.")
            if skipped:
                msg += "\n\nSkipped:\n" + "\n".join(
                    f"  {code}: {reason}" for code, reason in skipped)
            messagebox.showinfo("Insert complete", msg)
            self._refresh_action_state()
        elif kind == "error":
            self._busy = False
            failed_kind, exc = payload
            self.progress.configure(value=0)
            self.progress_label.configure(text="")
            self._set_status(f"{failed_kind} failed: {exc}")
            messagebox.showerror("Backend error", f"{failed_kind} failed:\n\n{exc}")
            if self.metrics:
                self.metrics.log("error", op=failed_kind, message=str(exc))
            self._refresh_action_state()

    # --- status strip actions --------------------------------------------

    def _refresh_status_async(self) -> None:
        if self.cfg is None or self._busy:
            return
        cfg = self.cfg

        def probe() -> dict:
            records = load_records(cfg.records_dir)
            conn = check_connection(cfg)
            return {"records": len(records), "conn": conn}

        self._run_worker(probe, kind="status")

    def _apply_status(self, payload: dict) -> None:
        n = payload.get("records", 0)
        conn = payload.get("conn", {})
        self.cache_label.configure(text=f"Cache: {n} record(s) at {self.cfg.cache_dir}")
        if conn.get("ok"):
            ver = conn.get("library_version")
            ver_txt = f" (library v{ver})" if ver is not None else ""
            self.zotero_label.configure(text=f"Zotero: reachable{ver_txt}",
                                        foreground="#1a7f37")
        else:
            self.zotero_label.configure(text=f"Zotero: {conn.get('message', 'unreachable')}",
                                        foreground="#b00020")

    def _sync_now(self) -> None:
        if self.cfg is None:
            messagebox.showinfo("No report", "Open a report first so the cache "
                                "location is known.")
            return
        cfg = self.cfg
        self.progress.configure(value=0)
        self.progress_label.configure(text="Starting sync\u2026")
        self._set_status("Syncing the Zotero cache\u2026")

        def do_sync() -> dict:
            return sync(cfg, verbose=False, on_progress=self._on_progress)

        self._run_worker(do_sync, kind="sync_done")

    def _refresh_action_state(self) -> None:
        """Disable conflicting actions while a worker is busy (A26)."""
        loaded = self.cfg is not None
        state = "disabled" if (self._busy or not loaded) else "normal"
        self.sync_button.configure(state=state)
        self.refresh_button.configure(state=state)
        for btn in self._workflow_buttons:
            try:
                btn.configure(state="disabled" if self._busy else "normal")
            except tk.TclError:
                pass                          # button was destroyed on a phase swap

    # --- settings dialog --------------------------------------------------

    def _open_settings(self) -> None:
        SettingsDialog(self.root, self.settings, on_save=self._apply_settings)

    def _apply_settings(self, settings: Settings) -> None:
        self.settings = settings
        save_settings(settings)
        # Re-resolve config so a backend change takes effect on the next op (A35).
        if self.report_path is not None:
            self.cfg = load_config(repo_root=self.report_path.parent,
                                   overrides=self.settings.config_overrides())
            self._refresh_status_async()
        self._set_status("Settings saved.")

    # --- misc -------------------------------------------------------------

    def _set_status(self, text: str) -> None:
        self.status_bar.configure(text=text)

    def _show_placeholder(self) -> None:
        """Show a hint in the empty document pane until a report is loaded."""
        load_hint = ("drag a Markdown report onto this window"
                     if self.drag_drop_available
                     else "use  File \u2192 Open  to choose a Markdown report")
        self.doc_text.delete("1.0", "end")
        self.doc_text.insert("1.0",
                             "\n   No report loaded.\n\n"
                             f"   To get started, {load_hint},\n"
                             "   or use  File \u2192 New from paste  to analyse "
                             "copied text.\n")

    def _instruction_text(self) -> str:
        """The getting-started guidance shown in the idle phase."""
        if self.drag_drop_available:
            load_step = ("Load a report \u2014 drag a Markdown file anywhere onto "
                         "this window, use  File \u2192 Open, or  File \u2192 New from "
                         "paste  to analyse copied text.")
        else:
            load_step = ("Load a report \u2014 use  File \u2192 Open  (drag-and-drop "
                         "is available on Windows only), or  File \u2192 New from "
                         "paste  to analyse copied text.")

        if self.report_path is None:
            return (
                "1.  Settings \u2192 Settings\u2026  \u2014 set your model API base "
                "URL, model name and token, and choose the search backend.\n\n"
                f"2.  {load_step}\n\n"
                "3.  Check the cache status above. If it is empty or out of date, "
                "click  Sync now  to update it from Zotero.\n\n"
                "4.  Select a passage of prose in the document on the left, then "
                "click  Propose citable sentences  below.")
        return (
            f"Report loaded: {self.report_path.name}\n"
            f"Project folder: {self.report_path.parent}\n\n"
            "Select a passage of prose in the document on the left (highlight it "
            "with the mouse), then click  Propose citable sentences  below. The "
            "model will suggest which sentences should carry a citation; you keep "
            "or drop each one before any search runs.\n\n"
            "Use  Sync now  first if the cache status above looks empty or stale.")

    def set_drag_drop_available(self, available: bool) -> None:
        """Record whether OS drag-and-drop is active and refresh the hints."""
        self.drag_drop_available = available
        if self.report_path is None:
            self._show_placeholder()
        if self.phase == "idle":
            self._show_idle_phase()

    # --- Stage A2 workflow: phases ---------------------------------------

    def _clear_workflow(self) -> None:
        for child in self.workflow.winfo_children():
            child.destroy()
        for child in self.action_bar.winfo_children():
            child.destroy()
        self._workflow_buttons = []
        self._wrap_labels = []

    def _cta_button(self, parent, text: str, command):
        """A prominent green 'call-to-action' button for the primary step."""
        return tk.Button(parent, text=text, command=command,
                         bg=CTA_BG, fg=CTA_FG, activebackground=CTA_ACTIVE_BG,
                         activeforeground=CTA_FG, disabledforeground="#d6eadd",
                         relief="flat", bd=0, cursor="hand2",
                         font=("TkDefaultFont", 11, "bold"), padx=12, pady=9)

    def _show_idle_phase(self) -> None:
        self.phase = "idle"
        self._clear_workflow()
        lbl = ttk.Label(self.workflow, text=self._instruction_text(),
                        wraplength=320, justify="left", anchor="nw",
                        foreground="#333")
        lbl.pack(fill="both", expand=True, padx=8, pady=8)
        btn = self._cta_button(self.action_bar, "Propose citable sentences\u2026",
                               self._propose)
        btn.pack(fill="x")
        self._workflow_buttons.append(btn)
        self._refresh_action_state()

    def _show_statements_phase(self) -> None:
        self.phase = "statements"
        self._clear_workflow()
        ttk.Label(self.workflow,
                  text=("Tick the sentences to cite, then  Find citations. "
                        "Adding and dropping here is free \u2014 no model call."),
                  wraplength=300, justify="left", foreground="#333").pack(
            fill="x", padx=8, pady=(8, 4))

        inner = self._scrollable(self.workflow)
        self.statement_vars = []
        for i, sent in enumerate(self.proposed):
            var = tk.BooleanVar(value=True)
            self.statement_vars.append(var)
            colour = CLAIM_COLOURS[i % len(CLAIM_COLOURS)]
            row = ttk.Frame(inner)
            row.pack(fill="x", padx=2, pady=2)
            ttk.Checkbutton(row, variable=var,
                            command=self._highlight_claims).pack(
                side="left", anchor="n", padx=(0, 4))
            # tk.Label (not ttk) so the colour-matched background renders on all
            # platforms; wraplength tracks the panel width (see _scrollable) so
            # the text fills the coloured box instead of wrapping after a few
            # words.
            lbl = tk.Label(row, text=sent, justify="left", anchor="w",
                           background=colour, padx=4, pady=2)
            lbl.pack(side="left", fill="x", expand=True)
            self._wrap_labels.append((lbl, 60))

        # Actions live in the always-visible bar below the panel. "Find
        # citations" is the primary step, so it gets the green CTA treatment;
        # "Start over" lives in the File menu (a global control).
        add_btn = ttk.Button(self.action_bar, text="Add selected sentence",
                             command=self._add_selected_sentence)
        add_btn.pack(fill="x", pady=(0, 6))
        find_btn = self._cta_button(self.action_bar, "Find citations",
                                    self._find_citations)
        find_btn.pack(fill="x")
        self._workflow_buttons += [add_btn, find_btn]
        self._highlight_claims()
        self._refresh_action_state()

    def _show_candidates_phase(self) -> None:
        self.phase = "candidates"
        self._clear_workflow()
        self.candidate_vars = {}
        # ``last_summary`` is the build_session payload {"session_dir", "summary"};
        # the per-claim candidates live in the nested run_search summary (A32).
        summary = (self.last_summary or {}).get("summary", self.last_summary or {})
        candidates = summary.get("candidates", [])
        recommendations = (self.last_summary or {}).get("recommendations", {})
        eval_error = (self.last_summary or {}).get("eval_error", "")
        n_cited = sum(1 for _c, status, cands in candidates
                      if status == "ok" and cands)
        n_reco = sum(len(v.get("keep", [])) for v in recommendations.values())
        self._load_review_into_tab(summary.get("review"))

        # Pre-load the cache records once so each candidate can offer "Open in
        # Zotero" (A13) and a DOI/URL link without re-reading the cache per row.
        try:
            self._rec_by_id = records_by_id(self.cfg.records_dir) if self.cfg else {}
        except OSError:
            self._rec_by_id = {}

        if eval_error:
            head = (f"{summary.get('searched', len(candidates))} claim(s) "
                    f"searched; {n_cited} have candidate sources. The evaluation "
                    "step failed, so nothing is pre-ticked \u2014 choose the "
                    "candidates to keep yourself, then Insert.")
        else:
            head = (f"{summary.get('searched', len(candidates))} claim(s) "
                    f"searched; {n_cited} have candidate sources, {n_reco} "
                    "recommended (pre-ticked). Adjust the ticks, then Insert. "
                    "The full review.md is on the left.")
        head_lbl = tk.Label(self.workflow, text=head, justify="left", anchor="w",
                            foreground="#333")
        head_lbl.pack(fill="x", padx=8, pady=(8, 4))
        self._wrap_labels.append((head_lbl, 24))

        inner = self._scrollable(self.workflow)
        for claim, status, cands in candidates:
            reco = recommendations.get(claim.code, {})
            self._render_claim_block(inner, claim, status, cands, reco)

        insert_btn = self._cta_button(self.action_bar, "Insert selected citations",
                                      self._insert_citations)
        insert_btn.pack(fill="x", pady=(0, 6))
        open_btn = ttk.Button(self.action_bar, text="Open review.md",
                              command=self._open_review)
        open_btn.pack(fill="x", pady=(0, 6))
        start_btn = ttk.Button(self.action_bar, text="Start over",
                               command=self._reset_workflow)
        start_btn.pack(fill="x")
        self._workflow_buttons += [insert_btn, open_btn, start_btn]
        self._refresh_action_state()

    def _render_claim_block(self, parent, claim, status: str, cands: list,
                            reco: dict | None = None) -> None:
        reco = reco or {}
        keep = set(reco.get("keep", []))
        box = ttk.LabelFrame(parent, text=f"{claim.code} \u00b7 {claim.claim_type}")
        box.pack(fill="x", padx=4, pady=4)
        sent_lbl = tk.Label(box, text=claim.sentence, justify="left", anchor="w",
                            foreground="#222")
        sent_lbl.pack(fill="x", padx=6, pady=(4, 2))
        self._wrap_labels.append((sent_lbl, 50))
        if status == "skip":
            ttk.Label(box, text="(common knowledge \u2014 not searched)",
                      foreground="#666").pack(anchor="w", padx=6, pady=(0, 4))
            return
        if not cands:
            ttk.Label(box, text="NEEDS SOURCE \u2014 no candidate found.",
                      foreground="#b00020").pack(anchor="w", padx=6, pady=(0, 4))
            return
        if reco.get("why"):
            why_lbl = tk.Label(box, text=f"Recommendation: {reco['why']}",
                              justify="left", anchor="w", foreground="#2e6b3e")
            why_lbl.pack(fill="x", padx=6, pady=(0, 4))
            self._wrap_labels.append((why_lbl, 50))

        for cand in cands:
            var = tk.BooleanVar(value=cand.code in keep)
            self.candidate_vars[cand.code] = var
            row = ttk.Frame(box)
            row.pack(fill="x", padx=6, pady=(2, 0))
            label = (f"{cand.code}   {cand.in_text}   "
                     f"[{cand.confidence}/{cand.tier}]")
            ttk.Checkbutton(row, text=label, variable=var).pack(side="left")
            rec = self._rec_by_id.get(cand.record_id)
            if rec is not None:
                ttk.Button(row, text="Open in Zotero", width=14,
                           command=lambda r=rec: self._open_in_zotero(r)).pack(
                    side="right")
                link = (rec.front_matter.get("doi") or "").strip()
                url = (f"https://doi.org/{link}" if link
                       else (rec.front_matter.get("url") or "").strip())
                if url:
                    self._make_link(row, url).pack(side="right", padx=(0, 6))
            extract = (cand.extracts[0] if cand.extracts else "")[:240]
            if extract:
                ex_lbl = tk.Label(box, text=extract, justify="left", anchor="w",
                                 foreground="#555")
                ex_lbl.pack(fill="x", padx=24, pady=(0, 2))
                self._wrap_labels.append((ex_lbl, 70))

    def _scrollable(self, parent):
        """A vertically scrollable frame; returns the inner frame to fill.

        Labels registered in ``self._wrap_labels`` have their wraplength updated
        to match the canvas width, so wrapped text fills the panel rather than a
        fixed narrow column. A mouse wheel scrolls the canvas while hovered.
        """
        canvas = tk.Canvas(parent, highlightthickness=0)
        bar = ttk.Scrollbar(parent, orient="vertical", command=canvas.yview)
        inner = ttk.Frame(canvas)
        inner.bind("<Configure>",
                   lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
        window = canvas.create_window((0, 0), window=inner, anchor="nw")

        def _on_canvas(event):
            canvas.itemconfigure(window, width=event.width)
            for lbl, reserve in self._wrap_labels:
                try:
                    lbl.configure(wraplength=max(120, event.width - reserve))
                except tk.TclError:
                    pass
        canvas.bind("<Configure>", _on_canvas)

        def _on_wheel(event):
            canvas.yview_scroll(int(-event.delta / 120), "units")
        canvas.bind("<Enter>",
                    lambda _e: canvas.bind_all("<MouseWheel>", _on_wheel))
        canvas.bind("<Leave>", lambda _e: canvas.unbind_all("<MouseWheel>"))

        canvas.configure(yscrollcommand=bar.set)
        canvas.pack(side="left", fill="both", expand=True, padx=(8, 0))
        bar.pack(side="right", fill="y")
        return inner

    # --- Stage A2 workflow: actions --------------------------------------

    def _make_client(self) -> "llm.LLMClient":
        return llm.LLMClient(self.settings.api_base_url, self.settings.model,
                             self.settings.resolved_token())

    def _selected_text(self) -> str:
        try:
            return self.doc_text.get("sel.first", "sel.last")
        except tk.TclError:
            return ""

    def _ready_for_llm(self) -> bool:
        if self.cfg is None:
            messagebox.showinfo("No report", "Open a report first.")
            return False
        if not self.settings.is_configured():
            messagebox.showinfo(
                "Model not configured",
                "Set the model API base URL, model name and token in "
                "Settings \u2192 Settings\u2026 first.")
            return False
        return True

    def _propose(self) -> None:
        if not self._ready_for_llm():
            return
        selection = self._selected_text()
        if not selection.strip():
            messagebox.showinfo(
                "Select some text",
                "Highlight a passage of prose in the document on the left, then "
                "click  Propose citable sentences.")
            return
        client = self._make_client()
        self._set_status("Asking the model for citable sentences\u2026")
        if self.metrics:
            self.metrics.log("propose_request", chars=len(selection))

        def work() -> tasks.ProposeResult:
            return tasks.propose_statements(client, selection)

        self._run_worker(work, kind="proposed")

    def _add_selected_sentence(self) -> None:
        selection = self._selected_text().strip()
        if not selection:
            messagebox.showinfo("Select some text",
                                "Highlight a sentence in the document to add it.")
            return
        # Pure-GUI add (A8): verify verbatim against the document, no model call.
        from refcite import sentence_matches
        document = self.doc_text.get("1.0", "end-1c")
        if not sentence_matches(document, selection):
            messagebox.showwarning(
                "Not a verbatim match",
                "That selection could not be matched verbatim in the document. "
                "Select a single clean sentence and try again.")
            return
        if selection in self.proposed:
            return
        self.proposed.append(selection)
        self._show_statements_phase()

    def _find_citations(self) -> None:
        if not self._ready_for_llm():
            return
        confirmed = [s for s, var in zip(self.proposed, self.statement_vars)
                     if var.get()]
        if not confirmed:
            messagebox.showinfo("Nothing selected",
                                "Tick at least one sentence to cite.")
            return
        client = self._make_client()
        cfg = self.cfg
        report_path = self.report_path
        document = self.doc_text.get("1.0", "end-1c")
        self.progress.configure(value=0)
        self.progress_label.configure(text="Generating claims\u2026")
        self._set_status("Classifying sentences and searching for sources\u2026")
        if self.metrics:
            self.metrics.log("find_citations_request", sentences=len(confirmed))

        def work() -> dict:
            claims = tasks.generate_claims(client, confirmed, document,
                                           str(report_path))
            result = orchestrator.build_session(cfg, claims, report_path,
                                                on_progress=self._on_progress)
            # LLM call 3 (A33): evaluate the generated review.md and attach the
            # per-claim recommendations. A failure here must not lose the search,
            # so it degrades to "no pre-ticked recommendations".
            review_path = result.get("summary", {}).get("review")
            recs: dict = {}
            eval_error = ""
            if review_path:
                try:
                    review_md = Path(review_path).read_text(encoding="utf-8")
                    recs = tasks.evaluate_review(client, review_md)
                except (OSError, llm.LLMError) as exc:
                    eval_error = str(exc)
            result["recommendations"] = recs
            result["eval_error"] = eval_error
            return result

        self._run_worker(work, kind="found")

    def _reset_workflow(self) -> None:
        self.proposed = []
        self.statement_vars = []
        self.session_dir = None
        self.last_summary = None
        self._clear_highlights()
        self._set_review_placeholder()
        self.left_nb.select(0)
        if self.metrics:
            self.metrics.log("reset")
        self._show_idle_phase()

    def _set_review_placeholder(self) -> None:
        """Reset the review tab to its 'not generated yet' message."""
        self.review_text.delete("1.0", "end")
        self.review_text.insert(
            "1.0", "review.md will appear here after you run Find citations.")

    def _load_review_into_tab(self, review_path) -> None:
        """Show the generated review.md in its tab and switch to it."""
        if not review_path:
            return
        try:
            text = Path(review_path).read_text(encoding="utf-8")
        except OSError:
            return
        self.review_text.delete("1.0", "end")
        self.review_text.insert("1.0", text)
        try:
            self.left_nb.select(self._review_tab)
        except tk.TclError:
            pass

    def _reset_result_tabs(self) -> None:
        """Hide the post-insert result tabs until there is something to show (A38)."""
        for tab in (getattr(self, "_cited_tab", None), getattr(self, "_refs_tab", None)):
            if tab is not None:
                try:
                    self.left_nb.hide(tab)
                except tk.TclError:
                    pass

    def _show_result_tabs(self) -> None:
        """Populate and reveal the Cited-text and References result tabs (A38).

        Shown in both input modes; for a pasted session (A37) they are the only
        way to retrieve the results, which the author copies out.
        """
        if self.report_path is not None:
            try:
                cited = self.report_path.read_text(encoding="utf-8")
            except OSError:
                cited = ""
            self.cited_text.delete("1.0", "end")
            self.cited_text.insert("1.0", cited)

        refs = ""
        ref_path = getattr(self.cfg, "references_file", None) if self.cfg else None
        if ref_path:
            try:
                refs = Path(ref_path).read_text(encoding="utf-8")
            except OSError:
                refs = ""
        self.refs_text.delete("1.0", "end")
        self.refs_text.insert(
            "1.0", refs or "references.md is empty (no references were added).")

        for tab in (self._cited_tab, self._refs_tab):
            try:
                self.left_nb.add(tab)            # re-show a hidden tab
            except tk.TclError:
                pass
        try:
            self.left_nb.select(self._cited_tab)
        except tk.TclError:
            pass

    def _open_review(self) -> None:
        if not self.last_summary:
            return
        review = self.last_summary.get("summary", self.last_summary).get("review")
        if not review:
            return
        path = Path(review)
        try:
            import os
            if hasattr(os, "startfile"):
                os.startfile(str(path))     # Windows
            else:
                import webbrowser
                webbrowser.open(path.as_uri())
        except OSError as exc:
            messagebox.showerror("Could not open review.md", f"{path}\n\n{exc}")

    def _make_link(self, parent, url: str) -> tk.Label:
        """A small underlined clickable label that opens ``url`` (A13)."""
        lbl = tk.Label(parent, text="DOI/URL", foreground="#1a3d7c",
                       cursor="hand2")
        link_font = tkfont.Font(lbl, lbl.cget("font"))
        link_font.configure(underline=True)
        lbl.configure(font=link_font)
        lbl.bind("<Button-1>", lambda _e: self._open_uri(url))
        return lbl

    def _open_in_zotero(self, record) -> None:
        """Jump to ``record`` in the Zotero desktop app (A13)."""
        try:
            uri = zotero_select_uri(record, self.cfg)
        except (AttributeError, ValueError):
            return
        self._open_uri(uri)

    def _open_uri(self, uri: str) -> None:
        try:
            import os
            if hasattr(os, "startfile"):
                os.startfile(uri)           # Windows: handles custom schemes too
            else:
                import webbrowser
                webbrowser.open(uri)
        except OSError as exc:
            messagebox.showerror("Could not open link", f"{uri}\n\n{exc}")

    def _insert_citations(self) -> None:
        """LLM-free final step: write the ticked citations into the report."""
        if not self.session_dir:
            return
        selected = [code for code, var in self.candidate_vars.items()
                    if var.get()]
        if not selected and not messagebox.askyesno(
                "Nothing ticked",
                "No candidates are ticked, so no citation will be inserted and "
                "the session will be archived as reviewed. Continue?"):
            return
        cfg = self.cfg
        session_dir = self.session_dir
        self.progress.configure(value=0)
        self.progress_label.configure(text="Inserting citations\u2026")
        self._set_status("Writing citations into the report\u2026")
        if self.metrics:
            self.metrics.log("insert_request", selected=len(selected))

        def work() -> dict:
            return insert_session(cfg, session_dir, select=selected,
                                  verbose=False)

        self._run_worker(work, kind="inserted")

    def _highlight_claims(self) -> None:
        """Highlight just the ticked sentences in the document pane (A30).

        Each sentence gets a distinct cycling colour matching its statement row,
        and only the sentence span is tagged (not the whole paragraph).
        """
        self._clear_highlights()
        if self.phase != "statements":
            return
        for i, (sent, var) in enumerate(zip(self.proposed, self.statement_vars)):
            if not var.get():
                continue
            self._tag_sentence(sent, f"claim{i % len(CLAIM_COLOURS)}")

    def _tag_sentence(self, sentence: str, tag: str) -> None:
        """Tag the exact span of ``sentence`` in the document with ``tag``."""
        sent = sentence.strip()
        if not sent:
            return
        count = tk.IntVar()
        idx = self.doc_text.search(sent, "1.0", stopindex="end", count=count,
                                   exact=True)
        if idx:
            self.doc_text.tag_add(tag, idx, f"{idx}+{count.get()}c")
            return
        # Fallback to the whole line via the whitespace-equivalent matcher, in
        # case the sentence's spacing differs from the document's.
        line = tasks._find_line(self.doc_text.get("1.0", "end-1c"), sent)
        if line > 0:
            self.doc_text.tag_add(tag, f"{line}.0", f"{line}.end")

    def _clear_highlights(self) -> None:
        for i in range(len(CLAIM_COLOURS)):
            self.doc_text.tag_remove(f"claim{i}", "1.0", "end")

    def _make_readonly(self, text: tk.Text) -> None:
        """Block edits while keeping the widget selectable (A9/A11)."""
        allowed_nav = {"Left", "Right", "Up", "Down", "Home", "End", "Prior",
                       "Next", "Shift_L", "Shift_R", "Control_L", "Control_R"}

        def on_key(event):
            ctrl = (event.state & 0x4) != 0
            if ctrl and event.keysym.lower() in ("c", "a", "insert"):
                return None                  # allow copy / select-all
            if event.keysym in allowed_nav:
                return None
            return "break"

        text.bind("<Key>", on_key)
        for seq in ("<<Paste>>", "<<Cut>>", "<<Clear>>", "<Button-2>"):
            text.bind(seq, lambda _e: "break")

    def _show_about(self) -> None:
        messagebox.showinfo(
            "About Cite-Assist",
            "Cite-Assist \u2014 app mode (Stage A3: select \u2192 find \u2192 "
            "review \u2192 insert).\n\n"
            "A pure-stdlib desktop front end over the refcite backend.\n"
            "Select prose, propose citable sentences, find citations, then keep "
            "the recommended sources and Insert.\n"
            "Drag a report onto the window (Windows), use File \u2192 Open, or "
            "File \u2192 New from paste to analyse copied text.")


class SettingsDialog(tk.Toplevel):
    """Edit the minimal app settings (A24): main model + search backend."""

    def __init__(self, parent, settings: Settings, on_save):
        super().__init__(parent)
        self.title("Settings")
        self.transient(parent)
        self.resizable(False, False)
        self._on_save = on_save

        self.provider = tk.StringVar(value=detect_provider(settings.api_base_url))
        self.base_url = tk.StringVar(value=settings.api_base_url)
        self.model = tk.StringVar(value=settings.model)
        self.token = tk.StringVar(value=settings.token)
        self.backend = tk.StringVar(value=settings.search_backend)
        self.emb_url = tk.StringVar(value=settings.embeddings_url)
        self.emb_model = tk.StringVar(value=settings.embeddings_model)

        frm = ttk.Frame(self, padding=12)
        frm.pack(fill="both", expand=True)

        model_box = ttk.LabelFrame(frm, text="Main model (required)")
        model_box.pack(fill="x", pady=(0, 8))

        # Provider preset: a read-only dropdown that fills the editable fields.
        prov_row = ttk.Frame(model_box)
        prov_row.pack(fill="x", padx=8, pady=(6, 3))
        ttk.Label(prov_row, text="Provider", width=16, anchor="w").pack(side="left")
        prov_combo = ttk.Combobox(prov_row, textvariable=self.provider,
                                  values=list(PROVIDER_PRESETS), state="readonly",
                                  width=38)
        prov_combo.pack(side="left", fill="x", expand=True)
        prov_combo.bind("<<ComboboxSelected>>", lambda _e: self._apply_preset())

        # Base URL and model are editable comboboxes: pick a default or type one.
        all_urls = [p["api_base_url"] for p in PROVIDER_PRESETS.values()
                    if p["api_base_url"]]
        self._url_combo = self._combo_row(model_box, "API base URL", self.base_url,
                                          all_urls)
        self._model_combo = self._combo_row(model_box, "Model", self.model,
                                            self._preset_models())

        token_box = ttk.Frame(model_box)
        token_box.pack(fill="x", padx=8, pady=3)
        ttk.Label(token_box, text="API token", width=16, anchor="w").pack(side="left")
        ttk.Entry(token_box, textvariable=self.token, show="*", width=40).pack(
            side="left", fill="x", expand=True)

        # Test-connection row: a button plus a one-line coloured result.
        model_test_row = ttk.Frame(model_box)
        model_test_row.pack(fill="x", padx=8, pady=(2, 0))
        self._model_test_btn = ttk.Button(
            model_test_row, text="Test connection",
            command=self._test_main_model)
        self._model_test_btn.pack(side="left")
        self.model_test_result = ttk.Label(model_test_row, text="", wraplength=300,
                                           justify="left")
        self.model_test_result.pack(side="left", padx=(8, 0), fill="x", expand=True)

        # Live help text that changes with the selected provider.
        self.model_help = ttk.Label(model_box, text="", foreground="#555",
                                    wraplength=420, justify="left")
        self.model_help.pack(fill="x", padx=8, pady=(2, 8))
        self._update_model_help()

        backend_box = ttk.LabelFrame(frm, text="Search backend")
        backend_box.pack(fill="x", pady=(0, 8))
        ttk.Radiobutton(backend_box, text="BM25 (no server)", value="bm25",
                        variable=self.backend, command=self._toggle_emb).pack(
            anchor="w", padx=8, pady=2)
        ttk.Radiobutton(backend_box, text="Embeddings (needs a server)",
                        value="embeddings", variable=self.backend,
                        command=self._toggle_emb).pack(anchor="w", padx=8, pady=2)
        ttk.Label(backend_box,
                  text=("BM25 ranks with no server. Embeddings needs a running "
                        "model server (e.g. the same LM Studio) at the address below."),
                  foreground="#555", wraplength=420, justify="left").pack(
            fill="x", padx=8, pady=(0, 4))
        self.emb_frame = ttk.Frame(backend_box)
        self.emb_frame.pack(fill="x")
        self._row(self.emb_frame, "Embeddings URL", self.emb_url)
        self._row(self.emb_frame, "Embeddings model", self.emb_model)

        # Test-connection row for the embeddings server.
        emb_test_row = ttk.Frame(self.emb_frame)
        emb_test_row.pack(fill="x", padx=8, pady=(2, 6))
        self._emb_test_btn = ttk.Button(
            emb_test_row, text="Test connection",
            command=self._test_embeddings)
        self._emb_test_btn.pack(side="left")
        self.emb_test_result = ttk.Label(emb_test_row, text="", wraplength=300,
                                         justify="left")
        self.emb_test_result.pack(side="left", padx=(8, 0), fill="x", expand=True)
        self._toggle_emb()

        btns = ttk.Frame(frm)
        btns.pack(fill="x", pady=(4, 0))
        ttk.Button(btns, text="Save", command=self._save).pack(side="right")
        ttk.Button(btns, text="Cancel", command=self.destroy).pack(
            side="right", padx=(0, 6))

        self.grab_set()

    def _preset_models(self) -> list[str]:
        return list(PROVIDER_PRESETS.get(self.provider.get(), {}).get("models", []))

    def _apply_preset(self) -> None:
        """Fill the editable fields from the chosen provider preset."""
        preset = PROVIDER_PRESETS.get(self.provider.get(), {})
        url = preset.get("api_base_url", "")
        if url:                                  # "Custom" leaves fields untouched
            self.base_url.set(url)
        models = preset.get("models", [])
        self._model_combo.configure(values=models)
        if models:
            self.model.set(models[0])
        self._update_model_help()

    def _update_model_help(self) -> None:
        preset = PROVIDER_PRESETS.get(self.provider.get(), {})
        help_text = preset.get("help", "")
        if __import__("os").environ.get(TOKEN_ENV_VAR):
            help_text += (f"\n\nNote: {TOKEN_ENV_VAR} is set in your environment, "
                          "so the token field above is ignored.")
        self.model_help.configure(text=help_text)

    def _run_test(self, button, label, probe) -> None:
        """Run ``probe()`` off the UI thread and show the result on ``label``.

        ``probe`` returns ``(ok, message)``. The button is disabled while the
        check runs so it cannot be fired twice; the result is posted back onto
        the Tk thread with ``after`` (the worker thread never touches widgets).
        """
        button.configure(state="disabled")
        label.configure(text="Testing\u2026", foreground="#555")

        def worker() -> None:
            try:
                ok, message = probe()
            except Exception as exc:  # noqa: BLE001 — defensive; show any failure
                ok, message = False, f"unexpected error: {exc}"

            def show() -> None:
                if not label.winfo_exists():
                    return
                label.configure(text=message,
                                foreground="#1a7f37" if ok else "#b3261e")
                button.configure(state="normal")

            self.after(0, show)

        threading.Thread(target=worker, daemon=True).start()

    def _test_main_model(self) -> None:
        base = self.base_url.get().strip()
        model = self.model.get().strip()
        # The env var wins over the typed token, mirroring resolved_token (A29).
        token = __import__("os").environ.get(TOKEN_ENV_VAR) or self.token.get().strip()
        self._run_test(
            self._model_test_btn, self.model_test_result,
            lambda: connection.test_main_model(base, model, token))

    def _test_embeddings(self) -> None:
        url = self.emb_url.get().strip()
        model = self.emb_model.get().strip()
        token = __import__("os").environ.get(TOKEN_ENV_VAR) or self.token.get().strip()
        self._run_test(
            self._emb_test_btn, self.emb_test_result,
            lambda: connection.test_embeddings(url, model, token))

    def _combo_row(self, parent, label: str, var: tk.StringVar,
                   values: list[str]) -> "ttk.Combobox":
        row = ttk.Frame(parent)
        row.pack(fill="x", padx=8, pady=3)
        ttk.Label(row, text=label, width=16, anchor="w").pack(side="left")
        combo = ttk.Combobox(row, textvariable=var, values=values, width=40)
        combo.pack(side="left", fill="x", expand=True)
        return combo

    def _row(self, parent, label: str, var: tk.StringVar, *, show: str = "",
             hint: str = "") -> None:
        row = ttk.Frame(parent)
        row.pack(fill="x", padx=8, pady=3)
        ttk.Label(row, text=label, width=16, anchor="w").pack(side="left")
        entry = ttk.Entry(row, textvariable=var, width=40)
        if show:
            entry.configure(show=show)
        entry.pack(side="left", fill="x", expand=True)
        if hint:
            ttk.Label(parent, text=hint, foreground="#666").pack(
                anchor="w", padx=8)

    def _toggle_emb(self) -> None:
        state = "normal" if self.backend.get() == "embeddings" else "disabled"
        for child in self.emb_frame.winfo_children():
            for widget in child.winfo_children():
                if isinstance(widget, (ttk.Entry, ttk.Button)):
                    widget.configure(state=state)

    def _save(self) -> None:
        settings = Settings(
            api_base_url=self.base_url.get().strip(),
            model=self.model.get().strip(),
            token=self.token.get().strip(),
            search_backend=self.backend.get(),
            embeddings_url=self.emb_url.get().strip(),
            embeddings_model=self.emb_model.get().strip(),
        )
        self._on_save(settings)
        self.destroy()


class PasteDialog(tk.Toplevel):
    """Collect pasted prose to start an ephemeral scratch session (A36)."""

    def __init__(self, parent, on_submit):
        super().__init__(parent)
        self.title("New from paste")
        self.transient(parent)
        self._on_submit = on_submit

        frm = ttk.Frame(self, padding=12)
        frm.pack(fill="both", expand=True)
        ttk.Label(frm,
                  text=("Paste the prose to analyse. It is held in a temporary "
                        "scratch session \u2014 no file is saved in your folders; "
                        "copy the results from the tabs at the end."),
                  wraplength=460, justify="left", foreground="#333").pack(
            fill="x", pady=(0, 8))

        body = ttk.Frame(frm)
        body.pack(fill="both", expand=True, pady=(0, 8))
        self.text = tk.Text(body, wrap="word", width=64, height=18,
                            font=("TkFixedFont",))
        scroll = ttk.Scrollbar(body, orient="vertical", command=self.text.yview)
        self.text.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.text.pack(side="left", fill="both", expand=True)

        btns = ttk.Frame(frm)
        btns.pack(fill="x")
        ttk.Button(btns, text="Analyse pasted text", command=self._submit).pack(
            side="right")
        ttk.Button(btns, text="Cancel", command=self.destroy).pack(
            side="right", padx=(0, 6))

        self.text.focus_set()
        self.grab_set()

    def _submit(self) -> None:
        text = self.text.get("1.0", "end-1c").strip()
        if not text:
            messagebox.showinfo("Nothing pasted",
                                "Paste some text to analyse, or Cancel.")
            return
        self.destroy()
        self._on_submit(text)


def run() -> int:
    """Build the window and enter the Tk event loop. Returns a process exit code."""
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        print(f"Cite-Assist needs a display, and none is available ({exc}).")
        print("On Linux without tkinter installed: sudo apt install python3-tk")
        return 1

    root.title("Cite-Assist \u2014 app mode")
    root.geometry("1100x720")
    app = CiteApp(root)

    # Windows-only drag-and-drop enhancement (A21); other platforms use File \u2192 Open.
    enabled = dragdrop.enable_drop(root, app._on_drop)
    app.set_drag_drop_available(enabled)
    if not enabled:
        app._set_status("No report loaded \u2014 use File \u2192 Open or File \u2192 New "
                        "from paste (drag-and-drop is Windows-only).")

    root.mainloop()
    return 0
