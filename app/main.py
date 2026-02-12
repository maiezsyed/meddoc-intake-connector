"""
Delivery Finance App - Streamlit UI
====================================
A comprehensive UI for:
- Uploading and processing financial Excel workbooks
- Managing project scope and estimates
- Natural language Q&A powered by Gemini
"""

import hashlib
import json
import os
import re
import tempfile
import uuid
from datetime import datetime
from typing import Optional

import pandas as pd
import streamlit as st
from google.cloud import bigquery

# Import Vertex AI for Gemini
try:
    import vertexai
    from vertexai.generative_models import GenerativeModel, Part
    VERTEX_AI_AVAILABLE = True
except ImportError:
    VERTEX_AI_AVAILABLE = False

# =============================================================================
# CONFIGURATION
# =============================================================================

GCP_PROJECT_ID = os.environ.get("GCP_PROJECT_ID", "")
BQ_DATASET_ID = os.environ.get("BQ_DATASET_ID", "delivery_finance")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-1.5-pro")
CLOUD_RUN_REGION = os.environ.get("CLOUD_RUN_REGION", "us-central1")

# Sheet type detection patterns
SHEET_TYPE_PATTERNS = {
    'plan': [r'^plan$', r'plan\s*\(', r'allocation', r'forecast', r'staffing', r'20\d{2}.*plan', r'plan.*20\d{2}'],
    'rate_card': [r'rate\s*card', r'ratecard', r'custom.*rate', r'deptapps.*rate'],
    'actuals': [r'actual', r'timesheet', r'hours.*log', r'pivot'],
    'costs': [r'^costs?$', r'expense', r'vendor.*cost', r'^extras?$'],
    'investment_log': [r'invest.*log', r'investment\s+log', r'overrun'],
    'external_estimate': [r'ext.*estimate', r'client.*estimate', r'external.*summary', r'^ext\s+'],
    'media': [r'^media$', r'media.*plan', r'media.*buy'],
}

SKIP_SHEET_PATTERNS = [r'^_', r'helper', r'mapping', r'^info$']

# =============================================================================
# PAGE CONFIG
# =============================================================================

st.set_page_config(
    page_title="Delivery Finance Hub",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom CSS
st.markdown("""
<style>
    .main-header {
        font-size: 2.5rem;
        font-weight: 700;
        color: #1f2937;
        margin-bottom: 0.5rem;
    }
    .sub-header {
        font-size: 1.1rem;
        color: #6b7280;
        margin-bottom: 2rem;
    }
    .sheet-card {
        background: #f9fafb;
        border: 1px solid #e5e7eb;
        border-radius: 8px;
        padding: 1rem;
        margin-bottom: 0.5rem;
    }
    .sheet-type-badge {
        display: inline-block;
        padding: 0.25rem 0.75rem;
        border-radius: 9999px;
        font-size: 0.75rem;
        font-weight: 600;
        text-transform: uppercase;
    }
    .type-plan { background: #dbeafe; color: #1d4ed8; }
    .type-rate_card { background: #dcfce7; color: #16a34a; }
    .type-actuals { background: #fef3c7; color: #d97706; }
    .type-costs { background: #fce7f3; color: #db2777; }
    .type-skip { background: #f3f4f6; color: #6b7280; }
    .type-unknown { background: #fef2f2; color: #dc2626; }
    .chat-message {
        padding: 1rem;
        border-radius: 8px;
        margin-bottom: 0.5rem;
    }
    .user-message {
        background: #eff6ff;
        border-left: 4px solid #3b82f6;
    }
    .assistant-message {
        background: #f0fdf4;
        border-left: 4px solid #22c55e;
    }
    .stTabs [data-baseweb="tab-list"] {
        gap: 2rem;
    }
    .metric-card {
        background: white;
        border: 1px solid #e5e7eb;
        border-radius: 12px;
        padding: 1.5rem;
        text-align: center;
    }
</style>
""", unsafe_allow_html=True)

# =============================================================================
# SESSION STATE INITIALIZATION
# =============================================================================

if 'uploaded_file_data' not in st.session_state:
    st.session_state.uploaded_file_data = None
if 'sheet_info' not in st.session_state:
    st.session_state.sheet_info = []
if 'processing_results' not in st.session_state:
    st.session_state.processing_results = None
if 'chat_history' not in st.session_state:
    st.session_state.chat_history = []
if 'current_step' not in st.session_state:
    st.session_state.current_step = 'upload'
if 'project_metadata' not in st.session_state:
    st.session_state.project_metadata = {}

# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def detect_sheet_type(sheet_name: str) -> str:
    """Detect the type of sheet based on its name."""
    name_lower = sheet_name.lower().strip()

    for pattern in SKIP_SHEET_PATTERNS:
        if re.search(pattern, name_lower):
            return 'skip'

    for sheet_type, patterns in SHEET_TYPE_PATTERNS.items():
        for pattern in patterns:
            if re.search(pattern, name_lower):
                return sheet_type

    if re.search(r'20\d{2}', name_lower):
        return 'plan'

    return 'unknown'


def get_type_badge_class(sheet_type: str) -> str:
    """Get CSS class for sheet type badge."""
    return f"type-{sheet_type}" if sheet_type in ['plan', 'rate_card', 'actuals', 'costs', 'skip', 'unknown'] else 'type-unknown'


def generate_project_id(client_name: str, project_title: str, source_file: str) -> str:
    """Generate a deterministic project ID."""
    key = f"{client_name}|{project_title}|{source_file}"
    return hashlib.sha256(key.encode()).hexdigest()[:16]


@st.cache_resource
def get_bq_client():
    """Get BigQuery client (cached)."""
    if GCP_PROJECT_ID:
        return bigquery.Client(project=GCP_PROJECT_ID)
    return None


@st.cache_resource
def get_gemini_model():
    """Get Gemini model (cached)."""
    if not VERTEX_AI_AVAILABLE or not GCP_PROJECT_ID:
        return None
    try:
        vertexai.init(project=GCP_PROJECT_ID, location=CLOUD_RUN_REGION)
        return GenerativeModel(GEMINI_MODEL)
    except Exception as e:
        st.warning(f"Could not initialize Gemini: {e}")
        return None


def analyze_sheets(xlsx: pd.ExcelFile) -> list:
    """Analyze all sheets in an Excel file."""
    sheet_info = []

    for idx, sheet_name in enumerate(xlsx.sheet_names):
        detected_type = detect_sheet_type(sheet_name)

        try:
            df = pd.read_excel(xlsx, sheet_name=sheet_name, header=None, nrows=50)
            row_count = len(pd.read_excel(xlsx, sheet_name=sheet_name, header=None))
            col_count = len(df.columns)

            # Try to detect header row
            header_row = find_header_row(df, detected_type)

            # Get sample headers
            if header_row >= 0:
                headers = [str(h)[:30] for h in df.iloc[header_row].dropna().tolist()[:6]]
            else:
                headers = []

        except Exception:
            row_count = 0
            col_count = 0
            header_row = -1
            headers = []

        sheet_info.append({
            'index': idx,
            'name': sheet_name,
            'detected_type': detected_type,
            'row_count': row_count,
            'col_count': col_count,
            'header_row': header_row,
            'sample_headers': headers,
            'selected': detected_type not in ['skip', 'unknown', 'mapping', 'info'],
            'user_type_override': None,
            'year_tag': None,
            'notes': '',
        })

    return sheet_info


HEADER_KEYWORDS = {
    'plan': ['category', 'market', 'department', 'role', 'total hours', 'total fees'],
    'rate_card': ['market', 'craft', 'role', 'title', 'cost rate', 'bill rate', 'level'],
    'actuals': ['market', 'employee', 'role', 'total hours'],
    'costs': ['item', 'category', 'date', 'vendor', 'total cost'],
}


def find_header_row(df: pd.DataFrame, sheet_type: str, max_rows: int = 60) -> int:
    """Find the header row by scoring each row based on keyword matches."""
    keywords = HEADER_KEYWORDS.get(sheet_type, HEADER_KEYWORDS['plan'])
    best_row = -1
    best_score = 0

    for idx in range(min(max_rows, len(df))):
        row = df.iloc[idx]
        non_null = row.dropna()

        if len(non_null) < 3:
            continue

        score = 0
        string_count = 0

        for val in non_null:
            if isinstance(val, str):
                val_lower = val.lower().strip()
                string_count += 1

                for kw in keywords:
                    if kw in val_lower:
                        score += 5

        if string_count >= 4:
            score += string_count

        if score > best_score:
            best_score = score
            best_row = idx

    return best_row


# =============================================================================
# BIGQUERY FUNCTIONS
# =============================================================================

def query_similar_projects(scope_description: str, limit: int = 5) -> pd.DataFrame:
    """Query projects with similar scope descriptions."""
    client = get_bq_client()
    if not client:
        return pd.DataFrame()

    # Simple keyword-based search (in production, use Vector Search)
    keywords = [w.lower() for w in scope_description.split() if len(w) > 3]
    keyword_conditions = " OR ".join([f"LOWER(scope_description) LIKE '%{kw}%'" for kw in keywords[:10]])

    query = f"""
    SELECT
        project_id,
        client_name,
        project_title,
        scope_description,
        scope_tags,
        total_estimated_fees,
        total_estimated_hours,
        billing_type,
        start_date,
        end_date
    FROM `{GCP_PROJECT_ID}.{BQ_DATASET_ID}.projects`
    WHERE {keyword_conditions if keyword_conditions else "1=1"}
    ORDER BY ingested_at DESC
    LIMIT {limit}
    """

    try:
        return client.query(query).to_dataframe()
    except Exception as e:
        st.error(f"Query error: {e}")
        return pd.DataFrame()


def get_project_allocations(project_id: str) -> pd.DataFrame:
    """Get allocations for a specific project."""
    client = get_bq_client()
    if not client:
        return pd.DataFrame()

    query = f"""
    SELECT
        category,
        department,
        role,
        SUM(hours) as total_hours,
        AVG(bill_rate) as avg_bill_rate,
        SUM(estimated_fees) as total_fees
    FROM `{GCP_PROJECT_ID}.{BQ_DATASET_ID}.allocations`
    WHERE project_id = @project_id
    GROUP BY category, department, role
    ORDER BY total_hours DESC
    """

    job_config = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("project_id", "STRING", project_id)]
    )

    try:
        return client.query(query, job_config=job_config).to_dataframe()
    except Exception:
        return pd.DataFrame()


def get_dashboard_metrics() -> dict:
    """Get summary metrics for dashboard."""
    client = get_bq_client()
    if not client:
        return {}

    try:
        query = f"""
        SELECT
            COUNT(DISTINCT project_id) as total_projects,
            SUM(total_estimated_fees) as total_fees,
            SUM(total_estimated_hours) as total_hours
        FROM `{GCP_PROJECT_ID}.{BQ_DATASET_ID}.projects`
        """
        result = client.query(query).to_dataframe()

        if len(result) > 0:
            return {
                'total_projects': int(result.iloc[0]['total_projects'] or 0),
                'total_fees': float(result.iloc[0]['total_fees'] or 0),
                'total_hours': float(result.iloc[0]['total_hours'] or 0),
            }
    except Exception:
        pass

    return {'total_projects': 0, 'total_fees': 0, 'total_hours': 0}


def upload_project_to_bigquery(project_record: dict) -> bool:
    """Upload a project record to BigQuery."""
    client = get_bq_client()
    if not client:
        st.error("BigQuery client not available. Check GCP_PROJECT_ID configuration.")
        return False

    table_id = f"{GCP_PROJECT_ID}.{BQ_DATASET_ID}.projects"

    # Prepare the row with all required fields
    row = {
        'project_id': project_record.get('project_id', str(uuid.uuid4())),
        'client_name': project_record.get('client_name', 'Unknown'),
        'project_title': project_record.get('project_title', 'Untitled'),
        'project_number': project_record.get('project_number'),
        'company_code': project_record.get('company_code'),
        'market_region': project_record.get('market_region'),
        'rate_card_used': project_record.get('rate_card_used'),
        'billing_type': project_record.get('billing_type'),
        'start_date': project_record.get('start_date'),
        'end_date': project_record.get('end_date'),
        'scope_description': project_record.get('scope_description'),
        'scope_tags': project_record.get('scope_tags', []),
        'total_estimated_fees': project_record.get('total_estimated_fees'),
        'total_estimated_hours': project_record.get('total_estimated_hours'),
        'total_estimated_cost': project_record.get('total_estimated_cost'),
        'target_gross_margin': project_record.get('target_gross_margin'),
        'status': project_record.get('status', 'Draft'),
        'source_file': project_record.get('source_file'),
        'source_sheet': project_record.get('source_sheet'),
        'sheet_metadata': project_record.get('sheet_metadata'),
        'sheet_metadata_zone': json.dumps(project_record.get('sheet_metadata_zone')) if project_record.get('sheet_metadata_zone') else None,
        'pricing_panel_qa': json.dumps(project_record.get('pricing_panel_qa')) if project_record.get('pricing_panel_qa') else None,
        'extra_fields': json.dumps(project_record.get('extra_fields')) if project_record.get('extra_fields') else None,
        'ingested_at': datetime.utcnow().isoformat(),
    }

    try:
        errors = client.insert_rows_json(table_id, [row])
        if errors:
            st.error(f"BigQuery insert errors: {errors}")
            return False
        return True
    except Exception as e:
        st.error(f"BigQuery upload error: {e}")
        return False


def log_ingestion(file_name: str, sheet_name: str, sheet_type: str, status: str,
                  rows_processed: int = 0, error_message: str = None, user_metadata: str = None) -> bool:
    """Log ingestion to BigQuery ingestion_log table."""
    client = get_bq_client()
    if not client:
        return False

    table_id = f"{GCP_PROJECT_ID}.{BQ_DATASET_ID}.ingestion_log"

    row = {
        'ingestion_id': str(uuid.uuid4()),
        'source_file': file_name,
        'source_sheet': sheet_name,
        'sheet_type': sheet_type,
        'user_metadata': user_metadata,
        'rows_processed': rows_processed,
        'status': status,
        'error_message': error_message,
        'ingested_by': 'streamlit_app',
        'ingested_at': datetime.utcnow().isoformat(),
    }

    try:
        errors = client.insert_rows_json(table_id, [row])
        return len(errors) == 0
    except Exception:
        return False


# =============================================================================
# GEMINI CHAT FUNCTIONS
# =============================================================================

def build_context_prompt(user_query: str) -> str:
    """Build context prompt for Gemini based on user query."""
    # Get relevant projects
    similar_projects = query_similar_projects(user_query)

    context = """You are a helpful assistant for a delivery finance team. You help with:
- Finding similar past projects for estimating new work
- Analyzing project scope and estimates
- Answering questions about project financials and allocations
- Providing insights on resource planning

IMPORTANT: You must ONLY reference projects and data that are explicitly provided below.
Do NOT make up or hallucinate project names, costs, or any other data.
If no relevant projects are found in the database, say so clearly.
"""

    if len(similar_projects) > 0:
        context += f"\n\nFound {len(similar_projects)} relevant project(s) in the database:\n"
        for _, proj in similar_projects.iterrows():
            context += f"""
---
Project: {proj.get('project_title', 'N/A')}
Client: {proj.get('client_name', 'N/A')}
Scope: {proj.get('scope_description', 'N/A')[:500] if proj.get('scope_description') else 'N/A'}
Estimated Fees: ${proj.get('total_estimated_fees', 0):,.0f}
Estimated Hours: {proj.get('total_estimated_hours', 0):,.0f}
Billing Type: {proj.get('billing_type', 'N/A')}
"""
    else:
        context += "\n\nNO PROJECTS FOUND in the database matching this query. The database may be empty or no projects match the search terms. Please inform the user that no matching projects were found and suggest they upload project data first."

    return context


def chat_with_gemini(user_message: str) -> str:
    """Send message to Gemini and get response."""
    model = get_gemini_model()

    if not model:
        return "Gemini is not available. Please configure GCP_PROJECT_ID and ensure Vertex AI is enabled."

    # Build context
    context = build_context_prompt(user_message)

    # Build conversation history
    history = "\n".join([
        f"{'User' if msg['role'] == 'user' else 'Assistant'}: {msg['content']}"
        for msg in st.session_state.chat_history[-6:]  # Last 3 exchanges
    ])

    full_prompt = f"""{context}

Previous conversation:
{history}

User: {user_message}

Please provide a helpful, concise response. If referencing specific projects, include their names and key metrics."""

    try:
        response = model.generate_content(full_prompt)
        return response.text
    except Exception as e:
        return f"Error getting response: {str(e)}"


# =============================================================================
# UI COMPONENTS
# =============================================================================

def render_sidebar():
    """Render the sidebar navigation."""
    with st.sidebar:
        st.markdown("## Navigation")

        page = st.radio(
            "Go to",
            ["Dashboard", "Upload & Process", "Projects", "Chat Assistant"],
            label_visibility="collapsed"
        )

        st.divider()

        # Show quick metrics
        metrics = get_dashboard_metrics()
        st.metric("Total Projects", metrics['total_projects'])
        st.metric("Total Estimated Fees", f"${metrics['total_fees']:,.0f}")

        st.divider()

        st.caption("Delivery Finance Hub v1.0")

        return page


def render_dashboard():
    """Render the dashboard page."""
    st.markdown('<p class="main-header">Dashboard</p>', unsafe_allow_html=True)
    st.markdown('<p class="sub-header">Overview of your project estimates and financials</p>', unsafe_allow_html=True)

    metrics = get_dashboard_metrics()

    col1, col2, col3 = st.columns(3)

    with col1:
        st.metric(
            label="Total Projects",
            value=metrics['total_projects'],
            delta=None
        )

    with col2:
        st.metric(
            label="Total Estimated Fees",
            value=f"${metrics['total_fees']:,.0f}",
            delta=None
        )

    with col3:
        st.metric(
            label="Total Estimated Hours",
            value=f"{metrics['total_hours']:,.0f}",
            delta=None
        )

    st.divider()

    # Recent projects
    st.subheader("Recent Projects")

    client = get_bq_client()
    if client:
        try:
            query = f"""
            SELECT
                project_title,
                client_name,
                total_estimated_fees,
                total_estimated_hours,
                billing_type,
                ingested_at
            FROM `{GCP_PROJECT_ID}.{BQ_DATASET_ID}.projects`
            ORDER BY ingested_at DESC
            LIMIT 10
            """
            df = client.query(query).to_dataframe()

            if len(df) > 0:
                st.dataframe(
                    df,
                    column_config={
                        "project_title": "Project",
                        "client_name": "Client",
                        "total_estimated_fees": st.column_config.NumberColumn("Est. Fees", format="$%.0f"),
                        "total_estimated_hours": st.column_config.NumberColumn("Est. Hours", format="%.0f"),
                        "billing_type": "Billing Type",
                        "ingested_at": st.column_config.DatetimeColumn("Uploaded", format="MMM D, YYYY"),
                    },
                    hide_index=True,
                    use_container_width=True
                )
            else:
                st.info("No projects uploaded yet. Go to 'Upload & Process' to add your first project.")
        except Exception as e:
            st.warning(f"Could not load projects: {e}")
    else:
        st.info("Configure GCP_PROJECT_ID to connect to BigQuery.")


def render_upload_page():
    """Render the upload and processing page."""
    st.markdown('<p class="main-header">Upload & Process</p>', unsafe_allow_html=True)
    st.markdown('<p class="sub-header">Upload financial workbooks and select sheets to process</p>', unsafe_allow_html=True)

    # Step indicator
    step = st.session_state.current_step

    steps = ['upload', 'select', 'metadata', 'process', 'complete']
    step_labels = ['Upload File', 'Select Sheets', 'Project Details', 'Process', 'Complete']

    cols = st.columns(5)
    for i, (s, label) in enumerate(zip(steps, step_labels)):
        with cols[i]:
            if s == step:
                st.markdown(f"**{i+1}. {label}** ●")
            elif steps.index(s) < steps.index(step):
                st.markdown(f"~~{i+1}. {label}~~ ✓")
            else:
                st.markdown(f"{i+1}. {label}")

    st.divider()

    # STEP 1: Upload
    if step == 'upload':
        render_upload_step()

    # STEP 2: Select sheets
    elif step == 'select':
        render_select_step()

    # STEP 3: Metadata
    elif step == 'metadata':
        render_metadata_step()

    # STEP 4: Process
    elif step == 'process':
        render_process_step()

    # STEP 5: Complete
    elif step == 'complete':
        render_complete_step()


def render_upload_step():
    """Render file upload step."""
    st.subheader("Step 1: Upload Excel File")

    uploaded_file = st.file_uploader(
        "Choose an Excel file (.xlsx, .xls)",
        type=['xlsx', 'xls'],
        help="Upload a financial workbook containing plan, rate card, or actuals sheets"
    )

    if uploaded_file:
        with st.spinner("Analyzing file..."):
            # Save to temp file
            with tempfile.NamedTemporaryFile(delete=False, suffix='.xlsx') as tmp:
                tmp.write(uploaded_file.getvalue())
                tmp_path = tmp.name

            try:
                xlsx = pd.ExcelFile(tmp_path, engine='openpyxl')
                sheet_info = analyze_sheets(xlsx)

                st.session_state.uploaded_file_data = {
                    'name': uploaded_file.name,
                    'path': tmp_path,
                    'xlsx': xlsx,
                }
                st.session_state.sheet_info = sheet_info

                st.success(f"Found {len(sheet_info)} sheets in **{uploaded_file.name}**")

                # Show preview
                st.markdown("### Sheet Preview")

                for info in sheet_info:
                    type_class = get_type_badge_class(info['detected_type'])
                    selected_icon = "✅" if info['selected'] else "⬜"

                    with st.container():
                        col1, col2, col3 = st.columns([0.5, 3, 1])
                        with col1:
                            st.write(selected_icon)
                        with col2:
                            st.write(f"**{info['name']}**")
                            st.caption(f"Headers: {', '.join(info['sample_headers'][:4])}" if info['sample_headers'] else "No headers detected")
                        with col3:
                            st.markdown(f"<span class='sheet-type-badge {type_class}'>{info['detected_type']}</span>", unsafe_allow_html=True)

                if st.button("Continue to Sheet Selection →", type="primary"):
                    st.session_state.current_step = 'select'
                    st.rerun()

            except Exception as e:
                st.error(f"Error reading file: {e}")


def render_select_step():
    """Render sheet selection step."""
    st.subheader("Step 2: Select Sheets to Process")

    if not st.session_state.sheet_info:
        st.warning("No file uploaded. Please go back and upload a file.")
        if st.button("← Back to Upload"):
            st.session_state.current_step = 'upload'
            st.rerun()
        return

    st.info("Select which sheets to process and verify or change their detected types.")

    # Sheet selection
    for i, info in enumerate(st.session_state.sheet_info):
        with st.container():
            col1, col2, col3, col4 = st.columns([0.5, 2.5, 1.5, 1.5])

            with col1:
                selected = st.checkbox(
                    "",
                    value=info['selected'],
                    key=f"select_{i}",
                    label_visibility="collapsed"
                )
                st.session_state.sheet_info[i]['selected'] = selected

            with col2:
                st.write(f"**{info['name']}**")
                st.caption(f"{info['row_count']} rows, {info['col_count']} cols")

            with col3:
                type_options = ['plan', 'rate_card', 'actuals', 'costs', 'investment_log', 'external_estimate', 'skip']
                current_type = info['user_type_override'] or info['detected_type']
                new_type = st.selectbox(
                    "Type",
                    options=type_options,
                    index=type_options.index(current_type) if current_type in type_options else 0,
                    key=f"type_{i}",
                    label_visibility="collapsed"
                )
                st.session_state.sheet_info[i]['user_type_override'] = new_type

            with col4:
                # Year tag for plans
                if new_type == 'plan':
                    year = st.text_input(
                        "Year",
                        value=info.get('year_tag', ''),
                        key=f"year_{i}",
                        placeholder="e.g., 2025",
                        label_visibility="collapsed"
                    )
                    st.session_state.sheet_info[i]['year_tag'] = year

        st.divider()

    # Navigation
    col1, col2 = st.columns(2)
    with col1:
        if st.button("← Back to Upload"):
            st.session_state.current_step = 'upload'
            st.rerun()
    with col2:
        selected_count = sum(1 for s in st.session_state.sheet_info if s['selected'])
        if st.button(f"Continue with {selected_count} sheets →", type="primary", disabled=selected_count == 0):
            st.session_state.current_step = 'metadata'
            st.rerun()


def render_metadata_step():
    """Render project metadata input step."""
    st.subheader("Step 3: Project Details")

    st.info("Enter project information. This metadata will be associated with all processed sheets.")

    col1, col2 = st.columns(2)

    with col1:
        client_name = st.text_input(
            "Client Name *",
            value=st.session_state.project_metadata.get('client_name', ''),
            placeholder="e.g., Acme Corporation"
        )

        project_title = st.text_input(
            "Project Title *",
            value=st.session_state.project_metadata.get('project_title', ''),
            placeholder="e.g., 2025 Website Redesign"
        )

        project_number = st.text_input(
            "Project Number",
            value=st.session_state.project_metadata.get('project_number', ''),
            placeholder="e.g., PRJ-2025-001"
        )

    with col2:
        billing_type = st.selectbox(
            "Billing Type",
            options=['', 'Fixed Fee', 'T&M', 'Retainer', 'Hybrid'],
            index=0
        )

        start_date = st.date_input("Start Date", value=None)
        end_date = st.date_input("End Date", value=None)

    st.divider()

    scope_description = st.text_area(
        "Scope Description",
        value=st.session_state.project_metadata.get('scope_description', ''),
        height=150,
        placeholder="Describe the project scope, deliverables, and key objectives. This will be used for finding similar projects.",
        help="A detailed scope description helps the AI find similar past projects for comparison."
    )

    scope_tags = st.multiselect(
        "Scope Tags",
        options=['Website Redesign', 'Mobile App', 'Digital Transformation', 'E-commerce',
                 'Brand Strategy', 'Content Strategy', 'UX Research', 'Data & Analytics',
                 'AI/ML', 'Platform Build', 'Maintenance', 'Staff Augmentation'],
        default=st.session_state.project_metadata.get('scope_tags', [])
    )

    # Save metadata
    st.session_state.project_metadata = {
        'client_name': client_name,
        'project_title': project_title,
        'project_number': project_number,
        'billing_type': billing_type,
        'start_date': start_date.isoformat() if start_date else None,
        'end_date': end_date.isoformat() if end_date else None,
        'scope_description': scope_description,
        'scope_tags': scope_tags,
    }

    # Navigation
    col1, col2 = st.columns(2)
    with col1:
        if st.button("← Back to Sheet Selection"):
            st.session_state.current_step = 'select'
            st.rerun()
    with col2:
        can_continue = client_name and project_title
        if st.button("Process Sheets →", type="primary", disabled=not can_continue):
            st.session_state.current_step = 'process'
            st.rerun()

    if not can_continue:
        st.warning("Please fill in Client Name and Project Title to continue.")


def render_process_step():
    """Render processing step."""
    st.subheader("Step 4: Processing")

    selected_sheets = [s for s in st.session_state.sheet_info if s['selected']]

    if not selected_sheets:
        st.error("No sheets selected for processing.")
        return

    progress_bar = st.progress(0)
    status_text = st.empty()

    results = {
        'allocations': [],
        'rate_cards': [],
        'costs': [],
        'projects': [],
        'processing_log': [],
    }

    xlsx = st.session_state.uploaded_file_data['xlsx']
    file_name = st.session_state.uploaded_file_data['name']
    metadata = st.session_state.project_metadata

    for i, sheet_info in enumerate(selected_sheets):
        progress = (i + 1) / len(selected_sheets)
        progress_bar.progress(progress)
        status_text.text(f"Processing: {sheet_info['name']} ({i+1}/{len(selected_sheets)})")

        sheet_type = sheet_info['user_type_override'] or sheet_info['detected_type']

        if sheet_type == 'skip':
            continue

        try:
            df = pd.read_excel(xlsx, sheet_name=sheet_info['name'], header=None)
            header_row = sheet_info['header_row']

            if header_row < 0:
                results['processing_log'].append({
                    'sheet': sheet_info['name'],
                    'type': sheet_type,
                    'status': 'skipped',
                    'message': 'Could not detect header row'
                })
                continue

            # Generate project ID
            project_id = generate_project_id(
                metadata.get('client_name', ''),
                metadata.get('project_title', ''),
                file_name
            )

            # Create project record
            rows_processed = len(df) - header_row - 1
            project_record = {
                'project_id': project_id,
                'client_name': metadata.get('client_name'),
                'project_title': metadata.get('project_title'),
                'project_number': metadata.get('project_number'),
                'billing_type': metadata.get('billing_type'),
                'start_date': metadata.get('start_date'),
                'end_date': metadata.get('end_date'),
                'scope_description': metadata.get('scope_description'),
                'scope_tags': metadata.get('scope_tags', []),
                'total_estimated_hours': rows_processed,  # Placeholder - would come from actual parsing
                'source_file': file_name,
                'source_sheet': sheet_info['name'],
                'sheet_metadata': sheet_info.get('year_tag'),
                'status': 'Draft',
            }

            # Upload to BigQuery
            upload_success = upload_project_to_bigquery(project_record)

            if upload_success:
                results['projects'].append(project_record)
                results['processing_log'].append({
                    'sheet': sheet_info['name'],
                    'type': sheet_type,
                    'status': 'success',
                    'rows': rows_processed,
                    'message': f'{rows_processed} rows uploaded to BigQuery'
                })
                # Log successful ingestion
                log_ingestion(file_name, sheet_info['name'], sheet_type, 'success',
                             rows_processed, user_metadata=sheet_info.get('year_tag'))
            else:
                results['processing_log'].append({
                    'sheet': sheet_info['name'],
                    'type': sheet_type,
                    'status': 'error',
                    'message': 'Failed to upload to BigQuery'
                })
                log_ingestion(file_name, sheet_info['name'], sheet_type, 'failed',
                             error_message='BigQuery upload failed')

        except Exception as e:
            results['processing_log'].append({
                'sheet': sheet_info['name'],
                'type': sheet_type,
                'status': 'error',
                'message': str(e)
            })

    st.session_state.processing_results = results
    progress_bar.progress(1.0)
    status_text.text("Processing complete!")

    # Show summary
    st.success(f"Processed {len([l for l in results['processing_log'] if l['status'] == 'success'])} sheets successfully!")

    # Processing log
    with st.expander("Processing Log"):
        for log in results['processing_log']:
            icon = "✅" if log['status'] == 'success' else "❌" if log['status'] == 'error' else "⏭️"
            row_count = log.get('rows', 0)
            message = log.get('message', f"{row_count} rows")
            st.write(f"{icon} **{log['sheet']}** ({log['type']}): {message}")

    if st.button("Continue →", type="primary"):
        st.session_state.current_step = 'complete'
        st.rerun()


def render_complete_step():
    """Render completion step."""
    st.subheader("Step 5: Complete")

    st.success("Processing complete! Your data is ready.")

    results = st.session_state.processing_results

    if results:
        col1, col2, col3 = st.columns(3)

        with col1:
            success_count = len([l for l in results['processing_log'] if l['status'] == 'success'])
            st.metric("Sheets Processed", success_count)

        with col2:
            total_rows = sum(l.get('rows', 0) for l in results['processing_log'])
            st.metric("Total Rows", total_rows)

        with col3:
            project_count = len(results.get('projects', []))
            st.metric("Projects Created", project_count)

    st.divider()

    col1, col2 = st.columns(2)

    with col1:
        if st.button("Upload Another File"):
            # Reset state
            st.session_state.uploaded_file_data = None
            st.session_state.sheet_info = []
            st.session_state.processing_results = None
            st.session_state.project_metadata = {}
            st.session_state.current_step = 'upload'
            st.rerun()

    with col2:
        if st.button("Go to Dashboard", type="primary"):
            st.rerun()


def render_projects_page():
    """Render the projects list page."""
    st.markdown('<p class="main-header">Projects</p>', unsafe_allow_html=True)
    st.markdown('<p class="sub-header">Browse and search uploaded project estimates</p>', unsafe_allow_html=True)

    # Search
    search = st.text_input("Search projects", placeholder="Search by client, project name, or scope...")

    client = get_bq_client()
    if not client:
        st.info("Configure GCP_PROJECT_ID to view projects from BigQuery.")
        return

    try:
        # Build query
        base_query = f"""
        SELECT
            project_id,
            client_name,
            project_title,
            scope_description,
            scope_tags,
            total_estimated_fees,
            total_estimated_hours,
            billing_type,
            source_file,
            ingested_at
        FROM `{GCP_PROJECT_ID}.{BQ_DATASET_ID}.projects`
        """

        if search:
            base_query += f"""
            WHERE LOWER(client_name) LIKE '%{search.lower()}%'
               OR LOWER(project_title) LIKE '%{search.lower()}%'
               OR LOWER(scope_description) LIKE '%{search.lower()}%'
            """

        base_query += " ORDER BY ingested_at DESC LIMIT 50"

        df = client.query(base_query).to_dataframe()

        if len(df) == 0:
            st.info("No projects found. Upload your first project in the 'Upload & Process' section.")
            return

        # Display projects
        for _, row in df.iterrows():
            with st.container():
                col1, col2 = st.columns([3, 1])

                with col1:
                    st.markdown(f"### {row['project_title']}")
                    st.caption(f"Client: {row['client_name']}")

                    if row.get('scope_description'):
                        st.write(row['scope_description'][:300] + "..." if len(str(row['scope_description'])) > 300 else row['scope_description'])

                    if row.get('scope_tags'):
                        tags = row['scope_tags'] if isinstance(row['scope_tags'], list) else []
                        st.write(" ".join([f"`{tag}`" for tag in tags]))

                with col2:
                    if row.get('total_estimated_fees'):
                        st.metric("Est. Fees", f"${row['total_estimated_fees']:,.0f}")
                    if row.get('total_estimated_hours'):
                        st.metric("Est. Hours", f"{row['total_estimated_hours']:,.0f}")
                    if row.get('billing_type'):
                        st.caption(f"Billing: {row['billing_type']}")

                st.divider()

    except Exception as e:
        st.error(f"Error loading projects: {e}")


def render_chat_page():
    """Render the chat assistant page."""
    st.markdown('<p class="main-header">Chat Assistant</p>', unsafe_allow_html=True)
    st.markdown('<p class="sub-header">Ask questions about projects, estimates, and find similar work</p>', unsafe_allow_html=True)

    # Example prompts
    with st.expander("Example questions you can ask"):
        st.markdown("""
        - "Show me similar projects to a website redesign for a healthcare company"
        - "What's a typical estimate for a 6-month mobile app development project?"
        - "Find projects with UX research and strategy components"
        - "How many hours do we typically allocate for discovery phases?"
        - "Compare estimates for e-commerce projects"
        """)

    # Chat history display
    chat_container = st.container()

    with chat_container:
        for message in st.session_state.chat_history:
            if message['role'] == 'user':
                st.markdown(f"""
                <div class="chat-message user-message">
                    <strong>You:</strong> {message['content']}
                </div>
                """, unsafe_allow_html=True)
            else:
                st.markdown(f"""
                <div class="chat-message assistant-message">
                    <strong>Assistant:</strong> {message['content']}
                </div>
                """, unsafe_allow_html=True)

    # Chat input
    user_input = st.chat_input("Ask about projects, estimates, or scope...")

    if user_input:
        # Add user message
        st.session_state.chat_history.append({
            'role': 'user',
            'content': user_input
        })

        # Get AI response
        with st.spinner("Thinking..."):
            response = chat_with_gemini(user_input)

        # Add assistant message
        st.session_state.chat_history.append({
            'role': 'assistant',
            'content': response
        })

        st.rerun()

    # Clear chat button
    if st.session_state.chat_history:
        if st.button("Clear Chat"):
            st.session_state.chat_history = []
            st.rerun()


# =============================================================================
# MAIN APP
# =============================================================================

def main():
    """Main application entry point."""
    # Sidebar navigation
    page = render_sidebar()

    # Render selected page
    if page == "Dashboard":
        render_dashboard()
    elif page == "Upload & Process":
        render_upload_page()
    elif page == "Projects":
        render_projects_page()
    elif page == "Chat Assistant":
        render_chat_page()


if __name__ == "__main__":
    main()
