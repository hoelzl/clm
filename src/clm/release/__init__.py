"""Per-topic solution release engine (issue #208).

Promotes a topic's frozen ``completed`` build artifacts into a per-cohort
destination, driven by a volatile per-channel *ledger* (release intent) and a
per-destination *frozen manifest* (release fact / freeze boundary). The build
side (the ``.clm-manifest.json`` provenance index it reads) lives in
:mod:`clm.core.provenance_manifest`.
"""
