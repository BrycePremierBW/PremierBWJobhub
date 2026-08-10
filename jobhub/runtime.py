"""Shared imports, paths and project-wide constants for Premier Brushworks JobHub."""
from __future__ import annotations

import base64
import hashlib
import html
import json
import os
import re
import shutil
import sqlite3
import tempfile
import zipfile
from datetime import date, datetime, timedelta
from io import BytesIO
from pathlib import Path
from urllib.parse import urlparse

from jobhub_time import jobhub_today

import pandas as pd
import psycopg2
import requests
import streamlit as st
from PIL import Image
from psycopg2.pool import ThreadedConnectionPool
from pypdf import PdfReader, PdfWriter
from pypdf.generic import BooleanObject, DictionaryObject, NameObject

PROJECT_ROOT = Path(__file__).resolve().parent.parent

if os.getenv("DATA_DIR"):
    DATA_DIR = os.getenv("DATA_DIR")
elif os.name == "nt":
    DATA_DIR = str(PROJECT_ROOT / "data")
elif Path("/var/data").exists():
    DATA_DIR = "/var/data"
else:
    DATA_DIR = str(PROJECT_ROOT / "data")

DB_PATH = os.path.join(DATA_DIR, "jobhub.db")
JOB_FILES_DIR = os.path.join(DATA_DIR, "job_files")
PHOTOS_DIR = os.path.join(DATA_DIR, "photos")
EXPORTS_DIR = os.path.join(DATA_DIR, "exports")

TEMPLATE_DIR = str(PROJECT_ROOT / "templates")
ASSET_DIR = str(PROJECT_ROOT / "assets")
PB_LOGO_BACKGROUND_IMAGE = os.path.join(ASSET_DIR, "PB_Logo_Main_PNG.png")

EQUIPMENT_TEMPLATE_PDF = os.path.join(TEMPLATE_DIR, "PB Master Checklist FILLABLE INITIAL.pdf")
PAINT_ORDER_TEMPLATE_PDF = os.path.join(TEMPLATE_DIR, "PB Paint and Materials Order Form fillable.pdf")
VARIATION_TEMPLATE_PDF = os.path.join(TEMPLATE_DIR, "PB Variation Form fillable.pdf")

for folder in (DATA_DIR, JOB_FILES_DIR, PHOTOS_DIR, EXPORTS_DIR):
    os.makedirs(folder, exist_ok=True)

PB_MENU_ICONS = {
    "Dashboard": "🏠",
    "Control Centre": "🎯",
    "Jobs": "🧾",
    "Job Folders": "📁",
    "Estimating": "💰",
    "Site Operations": "🛠️",
    "Reports": "📊",
    "Management": "⚙️",
    "AI Assistant": "🤖",
    "3D Building Mapper": "🏗️",
    "Building Progress Mapper": "🗺️",
    "Employee Portal": "👷",
}


def get_job_folder(job_number):
    folder = os.path.join(JOB_FILES_DIR, str(job_number))
    os.makedirs(folder, exist_ok=True)
    return folder
