# README image generation notes

These are project-bound narrative raster assets generated with ChatGPT’s built-in image generation tool on 2026-09-22. The target repository uses the `content-generation-modules` `0.3.1` visual contract, pinned to commit `32f7cc4e54588549cb536d3ab0439004b15d10bb`.

The visual direction follows the repository’s accepted prior-work signals from [Harness on Steroids](https://github.com/Pukujan/harness-on-steroids) and [Eval Lab](https://github.com/Pukujan/Eval-lab): anime-inspired editorial scenes, a recurring human and friendly robot, deep blue-violet evening light, warm accents, and a clean diagram that answers one reader question. The characters and compositions are original to this repository.

## `hero-generated.png`

- Role: wide README hero; orient the reader to the decision problem.
- Dimensions: 1536x1024 landscape.
- Exact title: `Lowest price is not the decision`.
- Exact subtitle: `Compare supply, evidence, and policy before you choose a route.`
- Prompt intent: show an anime-inspired maintainer and friendly robot comparing price, supply, and evidence as they converge into one inspectable recommendation.
- Composition: quiet text space on the left; characters at center-right; three clean signal cards and one highlighted route on the board.
- Alt text: `An anime-inspired maintainer and friendly robot compare price, supply, and evidence as they follow one inference route toward an inspectable recommendation.`
- README use: place below the title and opening promise.
- Rejection conditions: generic mountain adventure, provider dashboard, unsupported guarantee, abstract network without a human relationship, unreadable title/subtitle, or obscured decision path.
- Review: accepted because it names the core trade-off, shows the human/robot relationship, and makes the recommendation path legible at README scale.
- SHA-256: `7406aa4ec75f0872fdeb0aabeda52366221a9738dba6e465af2519b447810357`.

## `supporting-generated.png`

- Role: wide supporting banner; explain the price/supply/evidence trade-off.
- Dimensions: 1536x1024 landscape.
- Exact title: `Make the trade-off visible`.
- Exact subtitle: `Price, supply, and evidence belong in one decision.`
- Prompt intent: show a sparse cheap route and a well-supplied higher-cost route being compared before an inspectable recommendation is highlighted.
- Composition: characters at left-center; two route cards in the board; a clear arrow to the final recommendation on the right.
- Alt text: `An anime-inspired maintainer and friendly robot compare a sparse cheap route with a well-supplied route before highlighting an inspectable recommendation.`
- README use: place below the five-step mechanism explanation.
- Rejection conditions: route difference is unclear, character continuity breaks, final recommendation looks like a live provider guarantee, or copy becomes unreadable.
- Review: accepted because it explains a different part of the story from the hero and makes the ranking trade-off visible without relying on technical prose.
- SHA-256: `470f780451701f484f0e03966745a35391f7b833e2dd599bee9bb2b78e75a0d0`.

## Exact-copy rule

The title and subtitle are part of each asset’s orientation job. They are supplied verbatim to the image-generation prompt and recorded in the manifest. If a future generation changes their wording, spelling, or meaning, reject and regenerate instead of silently accepting a generic banner.

## Reuse rule

Do not copy these characters or scenes into another repository. Reuse the method—human relationship, exact title/subtitle, one decision question, responsive crop review, and evidence-bounded prompt—then generate target-specific assets from that repository’s own adapter.
