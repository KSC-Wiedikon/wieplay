"""Automatic analysis (PLAN §10): features from the video, rally proposals, their scores.

`metrics` is plain Python and is used by the API too. Everything else needs the `analysis`
extra (numpy, onnxruntime, scikit-learn) and runs on the worker only.
"""
