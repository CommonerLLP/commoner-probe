"""The source registry: every data source the freshness check covers."""
from . import data_publications, parliament, state_legal

SOURCES = parliament.SOURCES + state_legal.SOURCES + data_publications.SOURCES
