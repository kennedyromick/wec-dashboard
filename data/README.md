One folder per race: `data/<season>/<NN>_<circuit>/`, e.g. `data/2026/01_imola/`.

Put in each race folder:
- the Al Kamel "Analysis" CSV (any CSV with "analysis" in its name; if there are several, the highest "Hour N" is used),
- the official circuit map (a PDF or image with "map" in its name, e.g. `1_Circuit Map.pdf`),
- optionally `race.json` to override the name or the centreline: `{"name": "6 Hours of Imola", "circuit": "imola"}`.

The dashboard lists every race folder that has an Analysis file. The circuit (folder name after the
round number, lower-cased: `01_imola` -> `imola`) picks the centreline `tracks/<circuit>.geojson` and
sector boundaries `tracks/<circuit>.sectors.json`; add a centreline with `npm run fetch-track -- <circuit>`.

Classification CSVs can stay in the folder but are not used.

Any `*.csv` file in this folder is picked up by `npm run inspect`.
