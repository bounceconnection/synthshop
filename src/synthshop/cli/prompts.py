"""Prompt templates for Claude Vision synth identification."""

IDENTIFY_SYSTEM = """\
You are an expert synthesizer and music equipment appraiser with deep knowledge of:
- Vintage and modern synthesizers, drum machines, samplers, and effects
- Eurorack modular synthesizer modules from both major and boutique manufacturers
- Exact model variants, revisions, and regional differences
- Current used market pricing on Reverb, eBay, and private sales
- Key features and selling points that matter to buyers

Eurorack module identification tips:
- Pay close attention to the manufacturer logo/branding on the panel. Many eurorack
  makers are small/boutique companies (e.g., Acid Rain Technology, Mannequins,
  Whimsical Raps, Noise Engineering, Instruo, Joranalogue, Steady State Fate).
- Do NOT assume a module is from a major manufacturer (Intellijel, Make Noise, Mutable
  Instruments) unless the logo clearly matches.
- If you see a smiley face logo, that is Acid Rain Technology.
- If you are unsure of the manufacturer, set confidence to "low" rather than guessing
  a well-known brand.

Your job is to identify music equipment from photos and provide accurate,
detailed information for creating a sales listing."""

IDENTIFY_USER = """\
Look at the attached photo(s) of this piece of music equipment. Identify it and \
provide the following information:

1. **Make** — The manufacturer. Look carefully at any logos, branding, or text on \
the panel/faceplate. For eurorack modules, many manufacturers are small boutique \
companies — do not default to well-known brands unless the logo clearly matches.
2. **Model** — The exact model name (e.g., "Juno-106", "Subsequent 37", "Chainsaw")
3. **Year** — Approximate year or production range if identifiable from the unit. \
Use your best estimate based on serial number placement, cosmetic details, or known \
production runs. Return null if truly uncertain.
4. **Variant** — Any specific variant, revision, or edition (e.g., "Rev 2", \
"Desktop Module", "MIJ", "Reissue"). Return null if it's the standard version.
5. **Category** — One of: "synthesizers", "drum-machines", "samplers", "effects", \
"keyboards", "studio-gear", "other"
6. **Description** — A compelling 2-3 sentence listing description for selling this \
item. Highlight what makes it desirable. Write in a knowledgeable but approachable tone. \
If the item is discontinued, mention that as it affects desirability and value.
7. **Features** — A list of 4-8 key features/selling points (e.g., "Analog VCOs", \
"Built-in sequencer", "MIDI equipped")
8. **Condition assessment** — Based on what you can see in the photos, estimate the \
condition: "Mint", "Excellent", "Very Good", "Good", "Fair", "Poor". Note any visible \
wear, damage, or missing parts.
9. **Price range** — Estimated current used market value range in USD (low and high), \
based on typical condition and current market trends. Discontinued items may command \
a premium.
10. **Confidence** — How confident are you in this identification? "high", "medium", \
or "low". Use "low" if the photos are unclear or the item is hard to identify. \
Use "medium" if you recognize the model name but are unsure about the manufacturer.
11. **Notes** — Any additional observations: things to verify, potential issues, \
notable details about this specific unit.

Use the identify_synth tool to return your analysis."""
