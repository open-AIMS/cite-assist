# App-mode run instructions — {RUN_ID}

This is an **app-mode** evaluation. The orchestration lives in the `citeapp`
GUI, not in an agent, so there is no accept-all prompt to drive — you operate the
app yourself.

Model for this run: **{MODEL}**. Ranking for this lane: **{RANKING}**.

1. Make sure **Zotero desktop is running** and the library is synced.
2. Launch the app from the cite-assist repo:

   ```bash
   cd "{REPO}/archive/agent-app"
   python app.py
   ```

3. In the app settings, set the model to **{MODEL}** and the embeddings/ranking
   as configured for this lane, then open this run's report:

   `{REPORT_PATH}`

4. Run the workflow, accepting the proposed statements and the top candidate for
   each claim (mirror the agent-mode accept-all policy).
5. Save. The app writes the in-text citations into `{REPORT_FILE}` and the
   reference list into `{REFERENCES_FILE}` in this folder.

When finished, score the run from the tests repo root:

```bash
python harness/score.py {SANDBOX}
```
