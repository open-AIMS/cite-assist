# eResearch manual evaluation

These are the manual citation-search demonstrations used for the eResearch
presentation. They are separate from the earlier prototype evaluation harness.
One example sentence was tested under three workflow conditions. A second
model was also tested in the no-skill condition. This is a small demonstration,
not an automated benchmark or a controlled estimate of general performance.

## Conditions

Tests were performed manually in VS Code with GitHub Copilot, in a new test
folder unrelated to this repository. The rest of this project was not available
to the agent. The Zotero library remained static throughout these tests.

For the skill conditions, the corresponding complete agent folder was attached
using **Add Folder to Workspace**. This exposed the same files that deployment
of that skill would make available. The operator then manually added its
`SKILL.md` to the chat context and supplied the test prompt. Automatic skill
activation was not used: skills in an attached workspace did not activate
automatically. This arrangement made the selected skill explicit without
repeatedly moving its files into the test project.

The no-skill condition did not receive either citation skill. Its prompt gave
more context than the skill-condition prompts. The original prompts are
preserved below; these were condition-specific prompts, not identical inputs.

Every tool-approval request was approved. This differs from development trials,
where a deviating workflow could be stopped, diagnosed and used to revise the
skill. Those iterative development trials are not these evaluation runs.
The surviving notes do not specify all VS Code settings, global instructions,
or whether a fresh chat was used for every run. No exact independent replay is
claimed. The private Zotero library is not distributed with the repository.

## Recorded outcomes

The main recorded data were elapsed time, number of tool approvals ("allow"
calls), tokens, credits, and whether both preferred papers, Hedley et al. (2018)
and Drusch et al. (2012), appeared in the top three recommendations. Both papers
appeared in the top three in all four runs. This was my practical
success criterion, not a judgement that every recommended source or proposed
sentence was correct, or that the original sentence was fully supported.

Candidate summaries and citation recommendations in the result notes are the
agent's output, not citations I endorsed. My comments are labelled
separately. In particular, recommending another source in the final prose does
not mean that source appeared in the top three.

## Run records

Both phase 2 skills were tested at version `1.0.0`, recorded in `metadata.version`
in their respective `SKILL.md` files.
For the CLI skill, the version covers the whole folder, including its script.
Increase the version whenever instructions or bundled runtime code change, and
record the tested version in new run notes. Later versions do not retrospectively
update these results. No skill version applies to the no-skill runs.

With the current billing 100 Credits = $1 USD.

| Condition | Model | Skill version | Elapsed time | Approvals | Tokens | Credits | Both preferred papers in top 3 | Prompt | Results |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| No skill | ChatGPT Luna 6 | None | 6 min 44 sec | 16 | 46.1k | 1.7 | Yes | [Prompt](no-skill/test1/prompt.txt) | [Notes](no-skill/test1/results.md) |
| No skill | ChatGPT Sol 6.1 | None | 3 min 47 sec | 15 | 44.9k | 22 | Yes | [Prompt](no-skill/test1/prompt.txt) | [Notes](no-skill/test1/results.md) |
| Natural-language skill | ChatGPT Luna 6 | 1.0.0 | 4 min 13 sec | 11 | 50.7k | 1.8 | Yes | [Prompt](agent-no-code/test1/prompt.txt) | [Notes](agent-no-code/test1/results.md) |
| Skill with Python CLI | ChatGPT Luna 6 | 1.0.0 | 4 min 30 sec | 8 | 85k | 2.8 | Yes | [Prompt](agent-python-cli/test1/prompt.txt) | [Notes](agent-python-cli/test1/results.md) |

The notes contain an extract of the final recommendations from the agent, typically
the top three citations and its recommended cistation. 

## Recording provenance

The three retained highlight clips correspond to the Luna 6 runs, not the
additional Sol 6.1 no-skill run. They are accelerated, captioned excerpts for
presentation; their playback durations must not be used as task runtimes.
The date and time strings below are original recording filenames.

| Test folder | Original raw recording | Retained presentation clip |
| --- | --- | --- |
| `no-skill/test1` | `Luna-6-no-skill 2026-10-14 101613.mp4` | [No-skill highlights](no-skill/test1/vid-edited/Luna-6-no-skill-highlights.mp4) |
| `agent-no-code/test1` | `Luna-6-no-code-skill-2026-10-14 142348.mp4` | [Natural-language skill highlights](agent-no-code/test1/vid-edited/Luna-6-no-code-skill-highlights.mp4) |
| `agent-python-cli/test1` | `Luna-6-python-cli-skill 2026-10-14 170653.mp4` | [Python CLI highlights](agent-python-cli/test1/vid-edited/Luna-6-python-cli-skill-highlights.mp4) |

Raw recordings and their editing scripts, notes and overlay inputs are kept
together in the Git-ignored `_local/recordings/<condition>/test1/` tree.
They are not part of the public checkout. The matching condition and test
folder names connect them to the prompts and results here. Local editing
instructions explain how to regenerate a clip without overwriting it.
Temporary video-editing `progress.md` and `status.json` files are retained only
with that local working material; they are not evaluation results.

## Scope

The low-cost Luna model completed the no-skill task but made several missteps, 
whereas the CLI-assisted run made no observed missteps and used the supplied 
CLI for Zotero access rather than generating its own retrieval code. Recorded 
approvals fell from 16 to 8, with the no-code skill between them at 11. The 
CLI run did not use fewer tokens or credits than the no-skill Luna run, however
since the small Luna model is low cost the increase in tokens was not a big problem.

These differences indicate that the SKILLs are likely to be effective despite 
just only running a single test sentence. Across more examples, a CLI-assisted
agent is like to still make mistakes and an unguided agent could have a favourable
run. We didn't do more citation experiments because we ran out of time.

The presentation illustrates how a skill can guide an agent and how bundled
tools can capture repeatable parts of a workflow. It does not establish that
one approach is generally faster, more accurate or more thorough. In particular,
the earlier RAG implementations were not compared in these runs. Human review
of the selected references and their support for each claim remains necessary.