"""Automatic analysis (PLAN §10): features from the video, rally proposals, their scores,
who served and won each rally, sets and the gaps between rallies.

`metrics`, `scoring`, `gaps` and `tracks` are plain Python and used by the API too. Everything
else needs the `analysis` extra (numpy, onnxruntime, scikit-learn) and runs on the worker only.
"""
