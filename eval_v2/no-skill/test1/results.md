## Test 1

No citation skill was supplied; no skill version applies. Both preferred papers,
Hedley et al. (2018) and Drusch et al. (2012), appeared in the top three in each
run below. Candidate summaries are agent output, not citations I endorsed.

## No Skill - Sol 6.1
3 min 47 sec, 15 allow calls, 44.9k tokens, 22 credits. Best papers in top 3.

Hedley et al. (2018). Coral reef applications of Sentinel-2: Coverage, characteristics, bathymetry and benthic mapping with comparison to Landsat 8. Remote Sensing of Environment.
Best overall match. Explicitly connects five-day revisit and 10 m imagery with actual marine coverage and losses of usable imagery through clouds and sun glint. Includes Great Barrier Reef analyses. Estimates around 20 usable clear images annually for a large proportion of reefs, not a clear image every five days.

Drusch et al. (2012). Sentinel-2: ESA’s Optical High-Resolution Mission for GMES Operational Services. Remote Sensing of Environment.
Best primary source for mission specifications. Supports the nominal five-day revisit of the two-satellite constellation and 10 m resolution of the visible and near-infrared bands. Describes mission design rather than verified Australian marine coverage.

Caballero and Stumpf (2020). Towards Routine Mapping of Shallow Bathymetry in Environments with Variable Turbidity: Contribution of Sentinel-2A/B Satellites Mission. Remote Sensing.
Best direct support for the turbidity clause. Demonstrates how turbidity compromises bathymetry retrieval and how multi-scene compositing reduces its effects. Also explicitly discusses five-day revisit and 10 m mapping.

## No skill - Luna 6
6 min 44 sec, 46.1 k tokens, 1.7 credits. 16 allow calls Best papers in top 2

Hedley et al. (2018), “Coral reef applications of Sentinel-2: Coverage, characteristics, bathymetry and benthic mapping with comparison to Landsat 8.” DOI
Best overall fit: directly covers Sentinel-2’s reef applications, 5-day revisit and 10 m resolution. It reports coverage of 88% of the world’s reefs and an average of about 20 usable cloud-free images per year. It also discusses cloud and glint challenges.

Drusch et al. (2012), “Sentinel-2: ESA’s Optical High-Resolution Mission for GMES Operational Services.” DOI
Strong primary source for the mission specifications: nominal five-day revisit at the equator, four 10 m bands, and wide swath. It describes the mission design, not verified operational image availability.

My observation: the agent tried to access SQLite directly before eventually switching to API.


