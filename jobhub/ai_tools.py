"""JobHub AI, App Builder AI and controlled self-edit support.

Generated from the previously working JobHub monolith.
"""

from __future__ import annotations

from .runtime import *


def jobhub_ai_api_key():
    """Return a cleaned OpenAI API key from secrets/environment.

    Render copy/paste can accidentally save a trailing newline or the word
    "Bearer". OpenAI rejects those values in the Authorization header, so we
    clean them here before any request is made.
    """
    raw_key = ""
    try:
        if "OPENAI_API_KEY" in st.secrets:
            raw_key = st.secrets["OPENAI_API_KEY"]
    except Exception:
        raw_key = ""
    if not raw_key:
        raw_key = os.environ.get("OPENAI_API_KEY", "")

    key = str(raw_key or "").strip()
    if key.lower().startswith("bearer "):
        key = key[7:].strip()
    key = key.replace("\n", "").replace("\r", "").replace("\t", "").strip()
    return key

def jobhub_ai_model():
    try:
        if "OPENAI_MODEL" in st.secrets:
            return st.secrets["OPENAI_MODEL"]
    except Exception:
        pass
    return os.environ.get("OPENAI_MODEL", "gpt-5.5")

def jobhub_ai_context(selected_job_id=None):
    df = job_cost_summary_dataframe()
    lines = []

    if selected_job_id and not df.empty:
        selected = df[df["job_id"].astype(int) == int(selected_job_id)]
        if not selected.empty:
            r = selected.iloc[0]
            lines.append("SELECTED JOB SUMMARY")
            for col in [
                "Job No", "Job Name", "Builder / Client", "Status", "Leading Hand", "Start Date", "End Date",
                "Contract Value", "Actual Material Cost", "Actual Labour Cost", "Total Actual Cost",
                "Gross Profit", "Gross Profit %", "Timesheet Hours", "Estimated Labour Hours", "Remaining Labour Hours"
            ]:
                if col in selected.columns:
                    lines.append(f"{col}: {r.get(col, '')}")

            materials = df_query("""
                SELECT COALESCE(NULLIF(m.custom_product_code, ''), p.product_code, '') AS 'Product Code',
                       COALESCE(NULLIF(m.custom_product_name, ''), p.product_name, '') AS 'Product Name',
                       m.qty_required AS 'Qty Required',
                       m.qty_received AS 'Qty Received',
                       COALESCE(m.custom_unit_price, p.price_ex_gst, 0) AS 'Unit Price',
                       ROUND(CAST((COALESCE(m.qty_required, 0) * COALESCE(m.custom_unit_price, p.price_ex_gst, 0)) AS numeric), 2) AS 'Line Cost',
                       m.notes AS 'Notes'
                FROM material_entries m
                LEFT JOIN products p ON p.id = m.product_id
                WHERE m.job_id = ?
                ORDER BY m.id DESC
                LIMIT 50
            """, (selected_job_id,))
            if not materials.empty:
                lines.append("\nMATERIALS")
                lines.append(materials.to_csv(index=False))

            timesheets = df_query("""
                SELECT e.name AS 'Employee',
                       t.work_date AS 'Date',
                       t.total_hours AS 'Hours',
                       t.work_type AS 'Work Type',
                       t.status AS 'Status',
                       t.notes AS 'Notes'
                FROM timesheet_entries t
                LEFT JOIN employees e ON e.id = t.employee_id
                WHERE t.job_id = ?
                ORDER BY t.work_date DESC
                LIMIT 50
            """, (selected_job_id,))
            if not timesheets.empty:
                lines.append("\nTIMESHEETS")
                lines.append(timesheets.to_csv(index=False))
    else:
        if not df.empty:
            overview_cols = [
                "Job No", "Job Name", "Status", "Start Date", "End Date", "Contract Value",
                "Total Actual Cost", "Gross Profit", "Gross Profit %", "Timesheet Hours"
            ]
            lines.append("ALL JOBS OVERVIEW")
            lines.append(df[[c for c in overview_cols if c in df.columns]].head(60).to_csv(index=False))

    return "\n".join(lines)[:18000]



def app_builder_read_file(path, max_chars=12000):
    try:
        p = Path(path)
        if not p.exists() or not p.is_file():
            return ""
        text = p.read_text(encoding="utf-8", errors="ignore")
        if len(text) > max_chars:
            return text[:max_chars] + f"\n\n...[trimmed after {max_chars} characters]..."
        return text
    except Exception as e:
        return f"Could not read {path}: {e}"

def app_builder_file_tree():
    allowed = []
    try:
        root = Path(".")
        for p in root.rglob("*"):
            if p.is_file():
                name = str(p).replace("\\", "/")
                if "__pycache__" in name or ".git" in name or "pb_jobhub.db" in name or "secrets.toml" in name:
                    continue
                if name.endswith((".py", ".txt", ".toml", ".sql")):
                    allowed.append(name)
    except Exception:
        allowed = ["pb_jobhub_app.py", "requirements.txt", "SUPABASE_SCHEMA_MANUAL_BACKUP.sql"] + [str(p.relative_to(Path("."))) for p in Path("jobhub").rglob("*.py")]
    return sorted(allowed)[:80]

def app_builder_relevant_code_snippets(question, max_snippets=8, chars_per_snippet=1800):
    """
    Pulls relevant sections from pb_jobhub_app.py without sending the full app every time.
    """
    source = app_builder_read_file("pb_jobhub_app.py", max_chars=120000)
    modular_sources = []
    for module_path in sorted(Path("jobhub").rglob("*.py")):
        modular_sources.append(f"\n--- {module_path} ---\n" + app_builder_read_file(str(module_path), max_chars=60000))
    source += "".join(modular_sources)
    if not source:
        return ""

    terms = []
    for raw in re.findall(r"[A-Za-z_]{4,}", str(question).lower()):
        if raw not in ["this", "that", "with", "from", "your", "have", "will", "make", "need", "want", "please"]:
            terms.append(raw)

    priority_terms = [
        "streamlit", "supabase", "postgres", "connect", "df_query", "execute", "job", "employee",
        "timesheet", "estimate", "material", "product", "user", "login", "forecast", "ai", "openai"
    ]
    terms = list(dict.fromkeys(terms + priority_terms))

    snippets = []
    lines = source.splitlines()
    lower_lines = [l.lower() for l in lines]

    matched_indexes = []
    for i, line in enumerate(lower_lines):
        if any(t in line for t in terms):
            matched_indexes.append(i)

    # group nearby line matches
    used = set()
    for idx in matched_indexes:
        if len(snippets) >= max_snippets:
            break
        start = max(idx - 20, 0)
        end = min(idx + 60, len(lines))
        key = (start // 40, end // 40)
        if key in used:
            continue
        used.add(key)
        snippet = "\n".join(lines[start:end])
        if len(snippet) > chars_per_snippet:
            snippet = snippet[:chars_per_snippet] + "\n...[snippet trimmed]..."
        snippets.append(f"--- pb_jobhub_app.py lines approx {start+1}-{end} ---\n{snippet}")

    return "\n\n".join(snippets)

def app_builder_notes_context(limit=20):
    try:
        notes = df_query("""
            SELECT topic AS 'Topic', note AS 'Note', source AS 'Source', created_at AS 'Created'
            FROM app_builder_notes
            ORDER BY id DESC
            LIMIT ?
        """, (limit,))
        if notes.empty:
            return ""
        return notes.to_csv(index=False)
    except Exception:
        return ""

def save_app_builder_note(topic, note, source="Manual / AI"):
    execute("""
        INSERT INTO app_builder_notes (topic, note, source, created_at)
        VALUES (?, ?, ?, ?)
    """, (topic, note, source, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))



SELF_EDIT_ALLOWED_FILES = {
    "pb_jobhub_app.py",
    "requirements.txt",
    "SUPABASE_SCHEMA_MANUAL_BACKUP.sql",
    ".streamlit/config.toml",
}
SELF_EDIT_ALLOWED_FILES.update(p.as_posix() for p in Path("jobhub").rglob("*.py"))

def self_edit_safe_path(target_file):
    target_file = str(target_file or "").strip().replace("\\", "/")
    if target_file not in SELF_EDIT_ALLOWED_FILES:
        return None, f"File not allowed for self-edit: {target_file}"

    p = Path(target_file)
    if ".." in p.parts or p.is_absolute():
        return None, "Unsafe file path."

    return p, None

def self_edit_extract_json(text):
    """
    Extracts a JSON array from AI output.
    Expected format:
    [
      {
        "target_file": "pb_jobhub_app.py",
        "find": "exact old text",
        "replace": "new text",
        "reason": "why"
      }
    ]
    """
    raw = str(text or "").strip()

    # Remove markdown fences if present.
    raw = re.sub(r"^```(?:json)?", "", raw.strip(), flags=re.I).strip()
    raw = re.sub(r"```$", "", raw.strip()).strip()

    try:
        data = json.loads(raw)
        if isinstance(data, dict) and "replacements" in data:
            data = data["replacements"]
        return data if isinstance(data, list) else []
    except Exception:
        pass

    # Try to find the first JSON array in text.
    start = raw.find("[")
    end = raw.rfind("]")
    if start != -1 and end != -1 and end > start:
        try:
            data = json.loads(raw[start:end+1])
            return data if isinstance(data, list) else []
        except Exception:
            pass

    return []

def self_edit_validate_replacements(replacements):
    issues = []
    if not replacements:
        issues.append("No replacement JSON found.")
        return issues

    for i, item in enumerate(replacements, start=1):
        if not isinstance(item, dict):
            issues.append(f"Replacement {i} is not an object.")
            continue

        target = item.get("target_file", "")
        find = item.get("find", "")
        replace = item.get("replace", "")

        path, error = self_edit_safe_path(target)
        if error:
            issues.append(f"Replacement {i}: {error}")

        if not find:
            issues.append(f"Replacement {i}: find text is empty.")

        if replace is None:
            issues.append(f"Replacement {i}: replace text is missing.")

        if path and path.exists():
            try:
                file_text = path.read_text(encoding="utf-8", errors="ignore")
                if find and find not in file_text:
                    issues.append(f"Replacement {i}: find text was not found in {target}.")
            except Exception as e:
                issues.append(f"Replacement {i}: could not read {target}: {e}")
        elif path:
            issues.append(f"Replacement {i}: target file does not exist: {target}")

    return issues

def self_edit_apply_replacements(replacements):
    """
    Applies exact find/replace patches.
    Creates backups first.
    If pb_jobhub_app.py compile fails, restores the backup.
    """
    result = {
        "applied": 0,
        "backups": [],
        "messages": [],
        "success": False,
    }

    issues = self_edit_validate_replacements(replacements)
    if issues:
        result["messages"].extend(issues)
        return result

    backup_root = Path(tempfile.gettempdir()) / "pb_jobhub_self_edit_backups"
    backup_root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    touched_files = set()

    try:
        for item in replacements:
            target_file = item["target_file"]
            find = item["find"]
            replace = item["replace"]

            path, error = self_edit_safe_path(target_file)
            if error:
                raise RuntimeError(error)

            if str(path) not in touched_files:
                backup_path = backup_root / f"{path.name}.{stamp}.bak"
                backup_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, backup_path)
                result["backups"].append(str(backup_path))
                touched_files.add(str(path))

            current = path.read_text(encoding="utf-8", errors="ignore")
            updated = current.replace(find, replace, 1)
            path.write_text(updated, encoding="utf-8")
            result["applied"] += 1
            result["messages"].append(f"Applied replacement to {target_file}: {item.get('reason', 'No reason provided')}")

        # Compile check and rollback for Python app file.
        if "pb_jobhub_app.py" in [str(item.get("target_file")) for item in replacements]:
            try:
                py_compile.compile("pb_jobhub_app.py", doraise=True)
                result["messages"].append("Python compile check passed after self-edit.")
            except Exception as compile_error:
                # Restore all backups.
                for backup in result["backups"]:
                    backup_path = Path(backup)
                    original_name = backup_path.name.split(".")[0]
                    if original_name == "pb_jobhub_app":
                        # backup filename is pb_jobhub_app.py.TIMESTAMP.bak
                        shutil.copy2(backup_path, Path("pb_jobhub_app.py"))
                result["messages"].append(f"Compile failed. Restored backup. Error: {compile_error}")
                return result

        result["success"] = True
        return result

    except Exception as e:
        result["messages"].append(f"Self-edit failed: {e}")
        return result

def save_app_code_change(title, request, ai_response, patch_json, target_files, status, result_message=""):
    execute("""
        INSERT INTO app_code_changes
        (title, request, ai_response, patch_json, target_files, status, created_at, applied_at, result_message)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        title,
        request,
        ai_response,
        patch_json,
        target_files,
        status,
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        datetime.now().strftime("%Y-%m-%d %H:%M:%S") if status == "Applied" else "",
        result_message,
    ))

def app_builder_self_edit_prompt(user_request):
    current_code = app_builder_relevant_code_snippets(user_request, max_snippets=12, chars_per_snippet=2500)
    file_tree = "\n".join(app_builder_file_tree())

    return f"""
You are App Builder AI for Premier Brushworks JobHub.

The user wants you to alter the app code. You must return ONLY valid JSON. No markdown. No explanation outside JSON.

Return a JSON array of exact text replacements:
[
  {{
    "target_file": "pb_jobhub_app.py",
    "find": "exact existing text to find",
    "replace": "replacement text",
    "reason": "short reason"
  }}
]

Rules:
- Only target these files: pb_jobhub_app.py, requirements.txt, SUPABASE_SCHEMA_MANUAL_BACKUP.sql, .streamlit/config.toml
- Use exact find text from the code context.
- Keep changes small and safe.
- If the request needs a large rebuild, return one small safe first step.
- Do not include secrets.
- Do not include markdown fences.
- Do not invent code locations that are not in context.

FILE TREE:
{file_tree}

RELEVANT CODE:
{current_code}

USER REQUEST:
{user_request}
"""

def app_builder_self_edit_section():
    st.subheader("Controlled Self-Edit")
    st.warning(
        "This lets App Builder AI apply exact code replacements to the running app files. "
        "On Streamlit Cloud, file changes may not permanently survive a redeploy unless you download the changed file and upload it to GitHub."
    )

    st.caption(
        "Safety: only exact text replacements are allowed, only approved files can be changed, "
        "a backup is created, and pb_jobhub_app.py is compile-checked after changes."
    )

    request = st.text_area(
        "What code change should the AI make?",
        height=140,
        placeholder="Example: Add a dashboard card showing jobs with missing timesheets this week.",
        key="self_edit_request",
    )

    if st.checkbox("Show relevant code context", value=False, key="self_edit_show_context"):
        st.code(app_builder_relevant_code_snippets(request or "jobhub app"), language="python")

    if st.button("Generate Self-Edit Patch", key="generate_self_edit_patch"):
        if not request.strip():
            st.error("Enter a code change request first.")
        else:
            prompt = app_builder_self_edit_prompt(request)
            with st.spinner("Generating safe code replacement JSON..."):
                answer, error = jobhub_ai_answer(prompt, "")

            if error:
                st.error(error)
            else:
                st.session_state["self_edit_ai_response"] = answer
                st.session_state["self_edit_request"] = request
                st.success("Patch proposal generated.")

    ai_response = st.session_state.get("self_edit_ai_response", "")
    stored_request = st.session_state.get("self_edit_request", request)

    if ai_response:
        st.markdown("### Proposed Patch JSON")
        st.code(ai_response, language="json")

        replacements = self_edit_extract_json(ai_response)
        issues = self_edit_validate_replacements(replacements)

        if issues:
            st.error("Patch is not ready to apply:")
            for issue in issues:
                st.write(f"- {issue}")
        else:
            st.success(f"Patch validated. {len(replacements)} replacement(s) ready.")
            preview_rows = []
            for i, item in enumerate(replacements, start=1):
                preview_rows.append({
                    "No": i,
                    "Target File": item.get("target_file", ""),
                    "Find Length": len(str(item.get("find", ""))),
                    "Replace Length": len(str(item.get("replace", ""))),
                    "Reason": item.get("reason", ""),
                })
            st.dataframe(pd.DataFrame(preview_rows), width="stretch", hide_index=True)

            confirm = st.text_input(
                "To apply this AI code change to the running app, type: APPLY CODE CHANGE",
                key="self_edit_confirm",
            )

            if st.button("Apply AI Code Change", key="apply_self_edit_patch"):
                if confirm.strip().upper() != "APPLY CODE CHANGE":
                    st.error("Type APPLY CODE CHANGE exactly before applying.")
                else:
                    result = self_edit_apply_replacements(replacements)
                    status = "Applied" if result["success"] else "Failed"
                    save_app_code_change(
                        title=stored_request[:100],
                        request=stored_request,
                        ai_response=ai_response,
                        patch_json=json.dumps(replacements, indent=2),
                        target_files=", ".join(sorted(set(str(x.get("target_file", "")) for x in replacements))),
                        status=status,
                        result_message="\n".join(result["messages"]),
                    )

                    if result["success"]:
                        st.success(f"Applied {result['applied']} code replacement(s).")
                        st.info("Download the changed file below and upload it to GitHub so the change persists after redeploy.")
                    else:
                        st.error("Patch was not applied or was rolled back.")

                    with st.expander("Self-edit result details", expanded=True):
                        for msg in result["messages"]:
                            st.write(msg)

    st.markdown("### Download Current App Files")
    for file_name in ["pb_jobhub_app.py", "requirements.txt", "SUPABASE_SCHEMA_MANUAL_BACKUP.sql"]:
        p, error = self_edit_safe_path(file_name)
        if p and p.exists():
            data = p.read_text(encoding="utf-8", errors="ignore").encode("utf-8")
            st.download_button(
                f"Download {file_name}",
                data=data,
                file_name=file_name,
                mime="text/plain",
                key=f"download_{file_name}",
            )

    st.markdown("### Code Change History")
    try:
        changes = df_query("""
            SELECT id AS 'ID',
                   title AS 'Title',
                   target_files AS 'Target Files',
                   status AS 'Status',
                   created_at AS 'Created',
                   result_message AS 'Result'
            FROM app_code_changes
            ORDER BY id DESC
            LIMIT 50
        """)
        if changes.empty:
            st.info("No code changes saved yet.")
        else:
            st.dataframe(changes, width="stretch", hide_index=True)
    except Exception:
        st.info("Code change history table will be available after the app initializes the database.")

def ai_secret(name, default=""):
    try:
        if name in st.secrets:
            return st.secrets[name]
    except Exception:
        pass
    return os.environ.get(name, default)

def ai_provider():
    """
    AI provider rules:
    - AI_PROVIDER=openai: use OpenAI online/cloud.
    - AI_PROVIDER=ollama: use local Ollama only.
    - AI_PROVIDER=auto or blank:
        * if OPENAI_API_KEY exists, use OpenAI
        * if hosted on Render and no OpenAI key, switch AI off
        * if running locally and no OpenAI key, use Ollama
    - AI_PROVIDER=none/off/disabled: switch AI off
    """
    provider = str(ai_secret("AI_PROVIDER", "auto")).strip().lower()

    if provider in ["none", "off", "disabled", "disable", "false", "0", "no", "no_ai", "no-ai"]:
        return "none"

    if provider not in ["ollama", "openai", "auto"]:
        provider = "auto"

    has_openai_key = bool(str(jobhub_ai_api_key() or "").strip())
    is_render = bool(os.getenv("RENDER") or os.getenv("RENDER_SERVICE_ID"))

    if provider == "openai":
        return "openai"

    if provider == "ollama":
        return "ollama"

    # auto mode
    if has_openai_key:
        return "openai"

    if is_render:
        return "none"

    return "ollama"

def ai_disabled_message():
    return (
        "AI is switched off on this hosted Render app because no OpenAI API key is configured. "
        "Add AI_PROVIDER=openai and OPENAI_API_KEY in Render Environment to use online AI. "
        "For free Ollama AI, run JobHub locally on the same computer as Ollama."
    )

def ollama_base_url():
    return str(ai_secret("OLLAMA_BASE_URL", "http://localhost:11434")).rstrip("/")

def ollama_model():
    return str(ai_secret("OLLAMA_MODEL", "llama3.2:3b")).strip() or "llama3.2:3b"

def ollama_timeout():
    try:
        return int(ai_secret("OLLAMA_TIMEOUT", "120"))
    except Exception:
        return 120

def openai_enabled():
    return bool(str(jobhub_ai_api_key() or "").strip())

def ollama_status():
    try:
        response = requests.get(f"{ollama_base_url()}/api/tags", timeout=5)
        if response.status_code == 200:
            return True, f"Ollama connected at {ollama_base_url()} using model {ollama_model()}."
        return False, f"Ollama responded with status {response.status_code}. Check Ollama is running."
    except Exception as e:
        return False, f"Ollama not reachable at {ollama_base_url()}. Start Ollama on this computer. Details: {e}"

def ai_backend_ready():
    provider = ai_provider()

    if provider == "none":
        return False, ai_disabled_message()

    if provider == "openai":
        if openai_enabled():
            return True, f"Using OpenAI online model {jobhub_ai_model()}."
        return False, "AI_PROVIDER is openai but OPENAI_API_KEY is missing."

    if provider == "ollama":
        return ollama_status()

    return False, ai_disabled_message()

def ai_cost_control_notice(context_key="global"):
    st.info(
        "AI Cost Control is on: manual JobHub features are free to use. OpenAI is only used when you press an AI button, "
        "tick the confirmation box, and there is no automatic re-run."
    )

def confirm_ai_api_spend(label="I understand this will use OpenAI API credit", key="confirm_ai_spend"):
    return st.checkbox(label, value=False, key=key)

def ollama_generate(prompt, system="", context="", model=None, timeout=None):
    if ai_provider() == "none":
        return None, ai_disabled_message()

    if ai_provider() == "openai":
        return None, "Ollama is not used in OpenAI mode. Use the JobHub AI Assistant or App Builder AI with OpenAI."

    model = model or ollama_model()
    timeout = timeout or ollama_timeout()

    full_prompt = ""
    if system:
        full_prompt += "SYSTEM:\n" + str(system).strip() + "\n\n"
    if context:
        full_prompt += "CONTEXT:\n" + str(context).strip() + "\n\n"
    full_prompt += "USER:\n" + str(prompt).strip()

    payload = {
        "model": model,
        "prompt": full_prompt,
        "stream": False,
    }

    try:
        response = requests.post(
            f"{ollama_base_url()}/api/generate",
            json=payload,
            timeout=timeout,
        )
        if response.status_code >= 400:
            return None, f"Ollama error {response.status_code}: {response.text[:1000]}"

        data = response.json()
        return data.get("response", "").strip(), None
    except Exception as e:
        return None, f"Ollama request failed: {e}"

def openai_responses_answer(prompt, context_text="", include_web=False, require_web=False, system_text=""):
    api_key = jobhub_ai_api_key()
    if not api_key:
        return None, "OPENAI_API_KEY is missing."

    payload = {
        "model": jobhub_ai_model(),
        "input": (
            (system_text or "You are a helpful assistant for Premier Brushworks JobHub.") +
            "\n\nCONTEXT:\n" + str(context_text or "") +
            "\n\nUSER REQUEST:\n" + str(prompt)
        ),
    }

    if include_web:
        payload["tools"] = [{"type": "web_search"}]
        payload["tool_choice"] = "required" if require_web else "auto"

    try:
        response = requests.post(
            "https://api.openai.com/v1/responses",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=payload,
            timeout=90,
        )
        if response.status_code >= 400:
            return None, f"OpenAI API error {response.status_code}: {response.text[:1000]}"

        data = response.json()
        if data.get("output_text"):
            return data["output_text"], None

        parts = []
        for item in data.get("output", []) or []:
            for content in item.get("content", []) or []:
                if isinstance(content, dict) and content.get("text"):
                    parts.append(str(content["text"]))

        return "\n".join(parts) if parts else json.dumps(data)[:3000], None
    except Exception as e:
        return None, f"OpenAI request failed: {e}"

def jobhub_ai_answer(question, context_text):
    system = (
        "You are JobHub AI for Premier Brushworks, a painting and decorating business. "
        "Use only the JobHub context provided. Give practical, direct advice for quoting, job costs, scheduling, "
        "materials, staffing, risks and next actions. If data is missing, say what is missing. Do not invent details."
    )

    provider = ai_provider()
    if provider == "none":
        return None, ai_disabled_message()

    if provider == "openai":
        return openai_responses_answer(question, context_text, include_web=False, require_web=False, system_text=system)

    return ollama_generate(question, system=system, context=context_text)

def app_builder_ai_call(question, include_web=False, require_web=False, selected_mode="Code Helper"):
    file_tree = "\n".join(app_builder_file_tree())
    reqs = app_builder_read_file("requirements.txt", max_chars=6000)
    schema = app_builder_read_file("SUPABASE_SCHEMA_MANUAL_BACKUP.sql", max_chars=12000)
    snippets = app_builder_relevant_code_snippets(question)
    saved_notes = app_builder_notes_context()

    system_prompt = f"""
You are App Builder AI inside Premier Brushworks JobHub.
You help improve and maintain this Streamlit + Supabase business app.

Rules:
- Be practical and direct.
- Help design features, find likely bugs, improve speed, improve database structure, and plan safe changes.
- If asked to change the app, provide a clear build plan and exact code/pseudocode sections.
- Do not pretend you have already changed GitHub or deployed the app.
- Do not expose or ask for secrets.
- If internet/web content is provided in context, use it and mention source URLs.
- If something is risky, say so and suggest the safest next step.
- This AI learns by saving notes in app_builder_notes. It does not retrain model weights.
Mode: {selected_mode}
"""

    context = f"""
APP FILE TREE:
{file_tree}

REQUIREMENTS:
{reqs}

DATABASE SCHEMA EXCERPT:
{schema}

RELEVANT CURRENT APP CODE SNIPPETS:
{snippets}

SAVED APP BUILDER LEARNINGS:
{saved_notes}
"""

    provider = ai_provider()
    if provider == "none":
        return None, ai_disabled_message()

    if provider == "openai":
        return openai_responses_answer(
            question,
            context,
            include_web=include_web,
            require_web=require_web,
            system_text=system_prompt,
        )

    if include_web:
        context += (
            "\n\nNOTE: Local Ollama mode does not have paid live web_search. "
            "Use the Internet Learning section with specific URLs to fetch pages for free and save notes."
        )

    return ollama_generate(question, system=system_prompt, context=context, timeout=ollama_timeout())

def fetch_web_page_text(url, max_chars=18000):
    """
    Free URL fetcher for internet learning.
    The user provides URLs. JobHub fetches the page and local Ollama summarises it.
    """
    url = str(url or "").strip()
    if not url:
        return "", "URL is blank."

    parsed = urlparse(url)
    if parsed.scheme not in ["http", "https"]:
        return "", "Only http and https URLs are allowed."

    try:
        response = requests.get(
            url,
            timeout=20,
            headers={
                "User-Agent": "PremierBrushworksJobHubLearningBot/1.0"
            }
        )
        if response.status_code >= 400:
            return "", f"Could not fetch URL. Status {response.status_code}"

        text = response.text
        text = re.sub(r"(?is)<script.*?>.*?</script>", " ", text)
        text = re.sub(r"(?is)<style.*?>.*?</style>", " ", text)
        text = re.sub(r"(?is)<noscript.*?>.*?</noscript>", " ", text)
        text = re.sub(r"(?s)<[^>]+>", " ", text)
        text = re.sub(r"&nbsp;", " ", text)
        text = re.sub(r"&amp;", "&", text)
        text = re.sub(r"&lt;", "<", text)
        text = re.sub(r"&gt;", ">", text)
        text = re.sub(r"\s+", " ", text).strip()

        if len(text) > max_chars:
            text = text[:max_chars] + "\n...[trimmed]..."

        return text, None
    except Exception as e:
        return "", f"Fetch failed: {e}"

def save_learning_source(topic, url, summary="", active=1):
    execute("""
        INSERT INTO app_learning_sources
        (topic, url, active, last_checked, last_summary, notes, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        topic,
        url,
        int(active),
        datetime.now().strftime("%Y-%m-%d %H:%M:%S") if summary else "",
        summary,
        "",
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    ))

def summarise_url_into_learning(topic, url):
    page_text, error = fetch_web_page_text(url)
    if error:
        return None, error

    prompt = (
        "Summarise this web page into practical JobHub learning notes for Premier Brushworks. "
        "Focus on what should be saved for future app building, quoting, cost forecasting, Streamlit, Supabase, "
        "Ollama/local AI, safety, or business operations. "
        "Return concise notes and include the source URL.\n\n"
        f"TOPIC: {topic}\nSOURCE URL: {url}\nPAGE TEXT:\n{page_text}"
    )

    answer, ai_error = app_builder_ai_call(
        question=prompt,
        include_web=False,
        require_web=False,
        selected_mode="Internet Learning Summariser",
    )
    if ai_error:
        return None, ai_error

    save_app_builder_note(topic, answer, source=f"URL: {url}")
    save_learning_source(topic, url, summary=answer, active=1)
    return answer, None

def free_local_ai_setup_page():
    st.header("Free Local AI Setup")
    st.caption("Use OpenAI online on Render, or Ollama for free local AI when running JobHub on your own computer.")

    status_ok, status_message = ai_backend_ready()

    c1, c2 = st.columns(2)
    c1.metric("AI Provider", ai_provider())
    c2.metric("OpenAI Model", jobhub_ai_model() if ai_provider() == "openai" else ollama_model())

    if status_ok:
        st.success(status_message)
    else:
        st.warning(status_message)

    st.markdown("### Recommended Streamlit Secrets")
    st.code(
        'AI_PROVIDER = "ollama"\n'
        'OLLAMA_BASE_URL = "http://localhost:11434"\n'
        'OLLAMA_MODEL = "llama3.2:3b"\n'
        'OLLAMA_TIMEOUT = "120"\n\n'
        '# Optional paid fallback only if you ever want it:\n'
        '# OPENAI_API_KEY = "sk-..."\n'
        '# OPENAI_MODEL = "gpt-5.5"\n',
        language="toml",
    )

    st.markdown("### Test Local AI")
    test_prompt = st.text_input("Test prompt", value="Say hello and confirm you are connected to JobHub.")
    if st.button("Test Ollama Local AI", key="test_ollama_ai"):
        answer, error = ollama_generate(test_prompt, system="You are a local AI test assistant.")
        if error:
            st.error(error)
        else:
            st.success("Local AI responded.")
            st.write(answer)

    st.markdown("### What free learning means")
    st.info(
        "The model learns by saving useful notes into JobHub's database. "
        "It does not retrain the AI model weights. Saved notes are reused as context in future AI answers."
    )

def app_builder_ai_page():
    st.header("App Builder AI")
    st.caption("Build, improve and learn for JobHub using free local Ollama AI by default.")

    status_ok, status_message = ai_backend_ready()
    if status_ok:
        st.success(status_message)
    else:
        st.warning(status_message)
        st.info("Open the Free Local AI Setup tab for install and connection steps.")

    section = st.radio(
        "Section",
        ["Build / Fix the App", "Self-Edit Code", "Internet Learning", "Saved Learnings", "Free Local AI Setup"],
        horizontal=True,
        key="app_builder_section",
    )

    if section == "Build / Fix the App":
        st.subheader("Build / Fix the App")
        mode = st.selectbox(
            "Mode",
            ["Code Helper", "Bug Fixer", "Feature Planner", "Speed Optimiser", "Database / Supabase Helper", "Streamlit UI Helper"],
            key="app_builder_mode",
        )

        include_web = False
        require_web = False

        if ai_provider() == "openai" or (ai_provider() == "auto" and openai_enabled()):
            include_web = st.checkbox("Allow OpenAI live internet research", value=True, key="app_builder_include_web")
            require_web = st.checkbox("Force OpenAI web search for this request", value=False, key="app_builder_require_web")
        else:
            st.info("Free local Ollama mode is active. For internet learning, use the Internet Learning tab with URLs.")

        quick = st.selectbox(
            "Quick request",
            [
                "Custom",
                "Review this app and suggest the next 5 improvements",
                "Help me make the app faster",
                "Help me add a new feature safely",
                "Review saved learning notes and suggest the best next JobHub upgrade",
                "Tell me what code files need changing for this feature",
            ],
            key="app_builder_quick",
        )
        default_question = "" if quick == "Custom" else quick

        question = st.text_area(
            "What do you want to build or fix?",
            value=default_question,
            height=150,
            placeholder="Example: Add a daily dashboard showing jobs starting this week, overdue invoices, missing timesheets and jobs at margin risk.",
            key="app_builder_question",
        )

        if st.checkbox("Show app code context being sent", value=False, key="app_builder_show_context"):
            st.markdown("### File tree")
            st.code("\n".join(app_builder_file_tree()))
            st.markdown("### Relevant snippets")
            st.code(app_builder_relevant_code_snippets(question or "jobhub app"))

        if st.button("Ask App Builder AI", key="ask_app_builder_ai"):
            if not question.strip():
                st.error("Enter a build/fix request first.")
            else:
                with st.spinner("App Builder AI is reviewing JobHub..."):
                    answer, error = app_builder_ai_call(
                        question=question,
                        include_web=include_web,
                        require_web=require_web,
                        selected_mode=mode,
                    )

                if error:
                    st.error(error)
                else:
                    st.markdown("### App Builder AI")
                    st.write(answer)

                    with st.expander("Save this as a learning note"):
                        note_topic = st.text_input("Topic", value=question[:80], key="save_ai_learning_topic")
                        note_text = st.text_area("Note to save", value=answer[:4000], height=200, key="save_ai_learning_text")
                        if st.button("Save Learning Note", key="save_ai_learning_button"):
                            save_app_builder_note(note_topic, note_text, source="App Builder AI")
                            st.success("Learning note saved.")

    elif section == "Self-Edit Code":
        app_builder_self_edit_section()

    elif section == "Internet Learning":
        st.subheader("Free Internet Learning by URL")
        st.caption("Paste useful URLs. JobHub fetches the page, local AI summarises it, and the learning is saved for future use.")

        with st.form("url_learning_form"):
            topic = st.text_input(
                "Learning topic",
                value="Streamlit / Supabase / JobHub app improvement",
            )
            urls_text = st.text_area(
                "URLs to learn from, one per line",
                height=140,
                placeholder="https://docs.streamlit.io/...\nhttps://docs.ollama.com/...",
            )
            submitted = st.form_submit_button("Fetch URLs, Summarise and Save Learning")

        if submitted:
            urls = [u.strip() for u in urls_text.splitlines() if u.strip()]
            if not urls:
                st.error("Paste at least one URL.")
            else:
                for url in urls:
                    st.markdown(f"### Learning from: {url}")
                    with st.spinner(f"Fetching and summarising {url}..."):
                        summary, error = summarise_url_into_learning(topic, url)
                    if error:
                        st.error(error)
                    else:
                        st.success("Saved learning note.")
                        st.write(summary)

        st.markdown("### Saved Learning Sources")
        sources = df_query("""
            SELECT id AS 'ID',
                   topic AS 'Topic',
                   url AS 'URL',
                   active AS 'Active',
                   last_checked AS 'Last Checked',
                   last_summary AS 'Last Summary'
            FROM app_learning_sources
            ORDER BY id DESC
            LIMIT 100
        """)
        if sources.empty:
            st.info("No learning sources saved yet.")
        else:
            st.dataframe(sources[["ID", "Topic", "URL", "Active", "Last Checked"]], width="stretch", hide_index=True)

            if st.button("Refresh All Active Learning Sources", key="refresh_learning_sources"):
                active_sources = sources[sources["Active"].astype(int) == 1]
                if active_sources.empty:
                    st.info("No active sources to refresh.")
                else:
                    for _, row in active_sources.iterrows():
                        st.markdown(f"Refreshing: {row['URL']}")
                        summary, error = summarise_url_into_learning(row["Topic"], row["URL"])
                        if error:
                            st.error(error)
                        else:
                            execute(
                                "UPDATE app_learning_sources SET last_checked = ?, last_summary = ? WHERE id = ?",
                                (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), summary, int(row["ID"]))
                            )
                            st.success("Refreshed and saved.")

    elif section == "Saved Learnings":
        st.subheader("Saved Learnings")
        notes = df_query("""
            SELECT id AS 'ID',
                   topic AS 'Topic',
                   source AS 'Source',
                   created_at AS 'Created',
                   note AS 'Note'
            FROM app_builder_notes
            ORDER BY id DESC
        """)

        if notes.empty:
            st.info("No saved learnings yet.")
        else:
            st.dataframe(notes[["ID", "Topic", "Source", "Created"]], width="stretch", hide_index=True)

            note_options = {f"{row['Topic']} | {row['Source']} | ID {row['ID']}": int(row["ID"]) for _, row in notes.iterrows()}
            selected = st.selectbox("Open learning note", list(note_options.keys()), key="open_learning_note")
            selected_id = note_options[selected]
            row = notes[notes["ID"].astype(int) == selected_id].iloc[0]
            st.markdown(f"### {row['Topic']}")
            st.caption(f"{row['Source']} • {row['Created']}")
            st.write(row["Note"])

            col1, col2 = st.columns(2)
            if col1.button("Delete This Learning Note", key="delete_learning_note"):
                execute("DELETE FROM app_builder_notes WHERE id = ?", (selected_id,))
                st.success("Learning note deleted.")
                refresh()

        with st.expander("Add manual learning note"):
            with st.form("manual_learning_note_form"):
                topic = st.text_input("Topic")
                source = st.text_input("Source", value="Manual")
                note = st.text_area("Note", height=180)
                submitted = st.form_submit_button("Save Manual Learning")
                if submitted:
                    if not topic.strip() or not note.strip():
                        st.error("Topic and note are required.")
                    else:
                        save_app_builder_note(topic, note, source=source)
                        st.success("Learning note saved.")
                        refresh()

    else:
        free_local_ai_setup_page()

def jobhub_ai_assistant_page():
    st.header("JobHub AI Assistant")
    st.caption("Ask an AI assistant about your JobHub data, job costs, quotes, scheduling and risks.")

    status_ok, status_message = ai_backend_ready()
    if status_ok:
        st.success(status_message)
    else:
        st.warning(status_message)
        st.info("For free mode, install Ollama and use App Builder AI > Free Local AI Setup.")
        return

    job_options = get_job_options()
    mode = st.radio("Context", ["All Jobs Overview", "Selected Job"], horizontal=True, key="ai_context_mode")
    selected_job_id = None

    if mode == "Selected Job":
        if not job_options:
            st.info("Create a job first.")
            return
        selected_job = st.selectbox("Select Job", list(job_options.keys()), key="ai_selected_job")
        selected_job_id = job_options[selected_job]

    quick = st.selectbox(
        "Quick Question",
        [
            "Custom",
            "Which jobs are at risk of running over budget?",
            "What should I check before quoting this job?",
            "How many painters do I need to finish this job on time?",
            "What materials or timesheets look unusual?",
            "Give me a director-level summary for this week.",
        ],
        key="ai_quick_question",
    )
    default_question = "" if quick == "Custom" else quick

    question = st.text_area(
        "Ask JobHub AI",
        value=default_question,
        height=120,
        placeholder="Example: Review this job and tell me the margin risk, labour pressure and next actions.",
        key="ai_question",
    )

    context_text = jobhub_ai_context(selected_job_id)
    learning_context = app_builder_notes_context(limit=20)
    if learning_context:
        context_text += "\n\nSAVED JOBHUB LEARNINGS:\n" + learning_context

    if st.checkbox("Show data being sent to AI", value=False, key="ai_show_context"):
        st.text_area("Context Preview", value=context_text, height=300)

    if st.button("Ask JobHub AI", key="ask_jobhub_ai"):
        if not question.strip():
            st.error("Enter a question first.")
        else:
            with st.spinner("JobHub AI is reviewing your data..."):
                answer, error = jobhub_ai_answer(question, context_text)
            if error:
                st.error(error)
            else:
                st.markdown("### Answer")
                st.write(answer)

