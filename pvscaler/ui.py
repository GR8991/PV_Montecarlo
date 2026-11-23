"""Small helpers that keep the app working across Streamlit versions."""
from __future__ import annotations

import inspect

import streamlit as st


def _version() -> tuple:
    try:
        return tuple(int(x) for x in st.__version__.split(".")[:2])
    except ValueError:
        return (0, 0)


def full(fn) -> dict:
    """Keyword arguments that make an element fill its container.

    Streamlit 1.50+ uses width="stretch" and is phasing out use_container_width;
    older versions only understand use_container_width.
    """
    try:
        if _version() >= (1, 50) and "width" in inspect.signature(fn).parameters:
            return {"width": "stretch"}
    except (TypeError, ValueError):
        pass
    return {"use_container_width": True}
