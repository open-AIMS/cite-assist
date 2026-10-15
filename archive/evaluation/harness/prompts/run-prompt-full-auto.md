# Run script — {IMPL}

An evaluation of the **{SKILL_NAME}** skill. Drive the agent using only the
user turns below, exactly as a real user would. The skill supplies the workflow —
do **not** add policies, hints, or extra guidance of your own, and do not paste
any of the setup notes into the chat.

**Setup (harness only — not a user turn)**

- Select model **{MODEL}** in the UI and do not change it during the run.
- The skill is installed in this workspace at `{SKILL_DIR}`.

---

**Prompt:**

> Please add citations to report.md

Accept python calls to refcite. Carefully review all other system calls.

**Prompt:**
> Please go with your suggestions.

**Prompt:**

> Please add those suggestions.

(Accept the suggestions the agent proposes.)
