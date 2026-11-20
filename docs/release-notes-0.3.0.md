# Deadlock Tools 0.3.0

- Ship editor with a bundle picker and grouped ability controls.
- Defender and Celestra support abilities with editable module settings.
- Deploy Plating preload repair for bundles created by earlier versions of the tool.
- Launch bay compatibility alignment and marker coordinate controls.
- Current database and prefab inspection and required DLL patch prompts.
- Workflow, command-line, and technical documentation.

## Release asset

Run `python scripts/build_pyz.py` to create `dist/deadlock.pyz`. The archive contains the Python package. Users supply their own Deadlock installation, configuration, and donor bundles.

Launch with `python deadlock.pyz gui`. See the repository README for setup. Some DLL patches target particular game builds. Bundle and DLL regression checks exercise the files, and a local game test is needed to validate battle behaviour.

Before making the repository public, review the repository's licence and decide which terms apply to the project code. Publish source, documentation, and the Python archive. Keep game files and generated mod bundles out of the repository and release assets.
