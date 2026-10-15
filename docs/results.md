The following are notes about the test results. This is simply the first round of tests that I did to see if the process would work. 

# Test 1

Reference sentence: 

`The European Space Agency Sentinel 2 satellite images most of Australia’s marine estate every five days at 10 m resolution, but individual scenes are frequently obscured by clouds, sun glint, turbid water and sensor noise.`

My initial expectation was to find Hedley et al. (2018) and
Drusch et al. (2012). The following was an initial citation-placement example,
not a verified final sentence or an endorsement that the sources support every
part of the claim. Later agent assessments below qualify the coverage claim.
The preferred-papers criterion was subsequently used in the conference runs;
see [the evaluation index](../eval_v2/README.md).

`The European Space Agency Sentinel 2 satellite images most of Australia’s marine estate every five days at 10 m resolution (Drusch et al., 2012), but individual scenes are frequently obscured by clouds, sun glint, turbid water and sensor noise. (Hedley et al., 2018)`

## Straight text search
`Sentinel 2` - Drusch et al., 2012 in position 32 out of 41
`Five days` - Drusch et al., 2012 in position 2 out of 3
`Sun glint` - Hedley et al., 2018 in position 4 out of 12
`turbid water` - Hedley et al., 2018 not found in 25 results


## ZotSeek
`The European Space Agency Sentinel 2 satellite images most of Australia’s marine estate every five days at 10 m resolution` - Drusch et al., 2012 in position 1 out of 20
`Sentinel 2 individual scenes are frequently obscured by clouds, sun glint, turbid water and sensor noise.`  - Hedley at al., 2018 in position in position 8 out of 20.

## Asking an LLM - No skill

Prompt:
I have an Zotero database running locally. I want help to find the best references for the following sentence from the database. This sentence probably requires two citations. Give me the top 10 papers to consider, please rank them.

The European Space Agency Sentinel 2 satellite images most of Australia’s marine estate every five days at 10 m resolution, but individual scenes are frequently obscured by clouds, sun glint, turbid water and sensor noise.


### ChatGPT Luna 6.1 in VSCode
The above prompt was provided into an empty workspace without access the cite-assist skill in `C:\Users\elawrey\.copilot\skills\cite-assist\SKILL.md`. Note these were initial test runs, not the ones used in the eResearch presentation. These tests were to determine that the agents were actually finding the papers as expected, i.e. that the agents were working as expected. The actual runs used in the presentation are recorded in `eval_v2`. 

Took 4 min 20 sec 55.2k tokens 2.1 credits
Agent output, top matches:
Hedley et al. (2018), “Coral reef applications of Sentinel-2: Coverage, characteristics, bathymetry and benthic mapping with comparison to Landsat 8.” Remote Sensing of Environment, 216, 598–614. DOI
Best overall fit. It explicitly discusses Sentinel-2’s 5-day revisit and 10 m multispectral resolution, reef coverage, and how clouds and surface glint affect the rate of usable images. It does not establish coverage of most of Australia’s entire marine estate.

Drusch et al. (2012), “Sentinel-2: ESA’s Optical High-Resolution Mission for GMES Operational Services.” Remote Sensing of Environment, 120, 25–36. DOI
Strong mission-specification source for spatial resolution and revisit. It focuses on the mission and terrestrial surfaces, rather than Australian marine coverage.

### ChatGPT Sol 6.1 in VSCode no-python-cli
Took 3 min 28 sec, 57.4 k tokens, 26.7 credits. Took 11 allow requests to run scripts generated.
Top matches:
Hedley et al. (2018). Coral reef applications of Sentinel-2: Coverage, characteristics, bathymetry and benthic mapping with comparison to Landsat 8.
Best overall match. Explicitly covers five-day revisit, 10 m resolution, acquisition coverage, and losses of usable imagery from cloud and glint. Includes Great Barrier Reef applications. Its global reef-coverage estimate is not an estimate of Australian marine-estate coverage.

Caballero and Stumpf (2020). Towards Routine Mapping of Shallow Bathymetry in Environments with Variable Turbidity: Contribution of Sentinel-2A/B Satellites Mission.
Best companion for the limitations clause. Discusses cloud and glint exclusions, turbidity-induced errors, NIR/SWIR instrument noise, and filtering. Also explicitly states the five-day revisit and 10 m visible-band resolution.

Drusch et al. (2012). Sentinel-2: ESA’s Optical High-Resolution Mission for GMES Operational Services.
Best mission-specification reference. Describes ESA’s mission, the two-satellite five-day revisit at the equator, and band-dependent resolutions of 10–60 m. Strong for the first clause, but not Australian marine coverage or the frequency of marine-image interference.

Agent-proposed final wording:
The European Space Agency’s Sentinel-2 constellation provides imagery with a nominal five-day revisit and 10 m spatial resolution in selected bands (Hedley et al., 2018), but marine observations are often limited by clouds, sun glint, water turbidity and sensor noise (Caballero & Stumpf, 2020).

My note: Caballero and Stumpf is OK, but not as authoritative as Drusch.



