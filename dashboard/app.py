"""
Streamlit Management and Monitoring Dashboard for AgentGate.
Provides real-time visibility and active controls across:
1. Live Timeline
2. Approvals Management
3. Session Detail & Provenance Inspector
4. Tamper-Evident Audit Verification
5. Policy Time Machine
6. Benchmark Results
7. Emergency Kill Switch
"""

import os
import sys
import json
import pandas as pd
import streamlit as st

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from gateway.storage import (
    list_sessions,
    get_session,
    init_db
)
from gateway.audit import get_audit_events, verify_audit_chain
from gateway.approvals import (
    list_approvals,
    resolve_approval,
    is_killswitch_active,
    set_killswitch
)
from gateway.provenance import get_session_provenance
from scripts.replay_policies import replay_policy

st.set_page_config(
    page_title="AgentGate | AI Agent Safety & Control",
    page_icon="🛡️",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Ensure database tables exist
init_db()

# --- Custom Styling ---
st.markdown("""
<style>
    .main-title { font-size: 2.2rem; font-weight: 700; color: #1E3A8A; margin-bottom: 0.2rem; }
    .sub-title { font-size: 1.05rem; color: #4B5563; margin-bottom: 1.5rem; }
    .badge-allow { background-color: #D1FAE5; color: #065F46; padding: 4px 8px; border-radius: 4px; font-weight: bold; }
    .badge-pause { background-color: #FEF3C7; color: #92400E; padding: 4px 8px; border-radius: 4px; font-weight: bold; }
    .badge-block { background-color: #FEE2E2; color: #991B1B; padding: 4px 8px; border-radius: 4px; font-weight: bold; }
    .card { background-color: #F9FAFB; padding: 16px; border-radius: 8px; border: 1px solid #E5E7EB; margin-bottom: 12px; }
</style>
""", unsafe_allow_html=True)

# --- Sidebar Navigation ---
st.sidebar.image("https://img.icons8.com/color/96/shield.png", width=64)
st.sidebar.title("AgentGate Controls")
page = st.sidebar.radio(
    "Navigation",
    [
        "📋 Live Timeline",
        "⏳ Approvals",
        "🔍 Session Detail",
        "🛡️ Audit Verification",
        "⏱️ Policy Time Machine",
        "📊 Benchmark Results",
        "🚨 Emergency Kill Switch"
    ]
)

# Global Kill Switch Status Banner
is_ks_active, ks_reason = is_killswitch_active()
if is_ks_active:
    st.error(f"🚨 **GLOBAL KILL SWITCH IS CURRENTLY ACTIVE!** All agent tool executions are blocked. Reason: {ks_reason}")


# ==============================================================================
# PAGE 1: LIVE TIMELINE
# ==============================================================================
if page == "📋 Live Timeline":
    st.markdown('<div class="main-title">Live Security Timeline</div>', unsafe_allow_html=True)
    st.markdown('<div class="sub-title">Real-time trace of intercepted agent tool requests and gateway decisions.</div>', unsafe_allow_html=True)

    events = get_audit_events(limit=100)
    if not events:
        st.info("No recorded audit events found in database. Execute the demo or benchmark to populate timeline.")
    else:
        st.write(f"Showing **{len(events)}** most recent events:")
        for ev in events:
            col1, col2, col3, col4 = st.columns([2, 2, 4, 2])
            with col1:
                st.write(f"**{ev['timestamp'][11:19]}**")
                st.caption(ev['event_id'])
            with col2:
                dec = ev.get("decision")
                if dec == "ALLOW":
                    st.markdown('<span class="badge-allow">ALLOW</span>', unsafe_allow_html=True)
                elif dec == "PAUSE":
                    st.markdown('<span class="badge-pause">PAUSE</span>', unsafe_allow_html=True)
                elif dec == "BLOCK":
                    st.markdown('<span class="badge-block">BLOCK</span>', unsafe_allow_html=True)
                else:
                    st.write(f"`{ev['event_type']}`")
            with col3:
                action_str = ev.get('action') or ev.get('event_type')
                st.write(f"**Action:** `{action_str}`")
                if ev.get("reason"):
                    st.caption(f"Reason: {ev['reason']}")
            with col4:
                sess_id = ev.get("session_id")
                st.caption(f"Session: {sess_id if sess_id else 'N/A'}")
            st.divider()


# ==============================================================================
# PAGE 2: APPROVALS
# ==============================================================================
elif page == "⏳ Approvals":
    st.markdown('<div class="main-title">Human Approvals Inbox</div>', unsafe_allow_html=True)
    st.markdown('<div class="sub-title">Review, approve, or reject paused actions requiring explicit human verification.</div>', unsafe_allow_html=True)

    status_filter = st.selectbox("Filter Status", ["ALL", "PENDING", "APPROVED", "REJECTED"], index=1)
    filter_val = None if status_filter == "ALL" else status_filter
    approvals = list_approvals(status=filter_val)

    if not approvals:
        st.success(f"No {status_filter.lower()} approval requests currently pending.")
    else:
        for app in approvals:
            with st.container():
                st.markdown(f"### Approval ID: `{app['id']}`")
                c1, c2 = st.columns([3, 1])
                with c1:
                    st.write(f"**Session:** `{app['session_id']}` | **Tool:** `{app['tool']}`")
                    st.info(f"**Impact Preview:** {app['impact_preview']}")
                    st.caption(f"Created: {app['created_at']} | Status: **{app['status']}**")
                    if app.get("decision_reasons"):
                        st.write("**Pause Reasons:**")
                        for r in app["decision_reasons"]:
                            st.write(f"- {r}")
                with c2:
                    if app["status"] == "PENDING":
                        if st.button("✅ Approve & Execute", key=f"app_{app['id']}", use_container_width=True):
                            res = resolve_approval(app["id"], action="approve")
                            if res.get("status") == "SUCCESS":
                                st.success("Action approved and executed successfully!")
                                st.rerun()
                            else:
                                st.error(res.get("message"))

                        if st.button("❌ Reject Action", key=f"rej_{app['id']}", use_container_width=True):
                            res = resolve_approval(app["id"], action="reject")
                            st.warning("Action rejected.")
                            st.rerun()
                    else:
                        st.write(f"Resolved: `{app['status']}`")
                        if app.get("resolved_at"):
                            st.caption(f"At: {app['resolved_at']}")
                st.divider()


# ==============================================================================
# PAGE 3: SESSION DETAIL
# ==============================================================================
elif page == "🔍 Session Detail":
    st.markdown('<div class="main-title">Session Trace & Provenance Inspector</div>', unsafe_allow_html=True)
    st.markdown('<div class="sub-title">Inspect multi-step action sequences, accumulated risk, and data provenance sources.</div>', unsafe_allow_html=True)

    sessions = list_sessions()
    if not sessions:
        st.info("No active or historical sessions found.")
    else:
        sess_options = {s["id"]: f"{s['id']} (Risk: {s['risk_score']}) - {s['user_instruction'][:40]}..." for s in sessions}
        selected_session_id = st.selectbox("Select Session to Inspect", list(sess_options.keys()), format_func=lambda x: sess_options[x])

        sess_detail = get_session(selected_session_id)
        if sess_detail:
            st.markdown(f"**Session ID:** `{sess_detail['id']}`")
            st.markdown(f"**Initial Instruction (Trusted):** *\"{sess_detail['user_instruction']}\"*")
            st.markdown(f"**Current Accumulated Risk Score:** `{sess_detail['risk_score']}` / 100")

            tab1, tab2 = st.tabs(["Action Sequence & Trace", "Data Provenance Registry"])

            with tab1:
                trace = sess_detail.get("trace", [])
                if not trace:
                    st.write("No actions executed in this session yet.")
                else:
                    for step in trace:
                        with st.expander(f"Step {step['step_index']}: {step['tool']} [{step['decision']}]", expanded=True):
                            st.write(f"**Arguments:** `{json.dumps(step['arguments'])}`")
                            st.write(f"**Decision:** `{step['decision']}` | **Risk Score:** `{step['risk_score']}`")
                            st.write("**Reasons:**")
                            for r in step["reasons"]:
                                st.write(f"- {r}")
                            if step.get("execution_result"):
                                st.write("**Execution Result:**")
                                st.json(step["execution_result"])

            with tab2:
                prov_records = get_session_provenance(selected_session_id)
                if not prov_records:
                    st.info("No provenance entities registered for this session.")
                else:
                    prov_df = pd.DataFrame(prov_records)
                    st.dataframe(prov_df[["source_id", "source_type", "trust_level", "extracted_value", "timestamp"]], use_container_width=True)
                    st.caption("Values originating from documents default to trust level 'untrusted', whereas explicit user instructions are 'user'.")


# ==============================================================================
# PAGE 4: AUDIT VERIFICATION
# ==============================================================================
elif page == "🛡️ Audit Verification":
    st.markdown('<div class="main-title">Tamper-Evident Audit Verification</div>', unsafe_allow_html=True)
    st.markdown('<div class="sub-title">Verify cryptographic SHA-256 hash chaining across all logged gateway events.</div>', unsafe_allow_html=True)

    if st.button("🔍 Run Chain Verification Check", use_container_width=True):
        res = verify_audit_chain()
        if res["verified"]:
            st.success(f"✅ **Audit Hash Chain Verified!** {res['details']}")
            st.metric("Verified Events", res["total_events"])
        else:
            st.error(f"❌ **TAMPERING DETECTED!** {res['details']}")
            st.warning(f"First Invalid Index: {res['first_invalid_index']} | Event ID: `{res['first_invalid_event_id']}`")

    st.markdown("### Hash Chain Sample")
    events = get_audit_events(limit=10)
    if events:
        audit_table = []
        for e in events:
            audit_table.append({
                "Event ID": e["event_id"],
                "Timestamp": e["timestamp"][:19],
                "Event Type": e["event_type"],
                "Prev Hash": f"{e['previous_hash'][:12]}...",
                "Current Hash": f"{e['current_hash'][:12]}..."
            })
        st.dataframe(pd.DataFrame(audit_table), use_container_width=True)


# ==============================================================================
# PAGE 5: POLICY TIME MACHINE
# ==============================================================================
elif page == "⏱️ Policy Time Machine":
    st.markdown('<div class="main-title">Policy Time Machine</div>', unsafe_allow_html=True)
    st.markdown('<div class="sub-title">Replay historical agent session traces against candidate policy rules without executing side-effects.</div>', unsafe_allow_html=True)

    policies_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "policies"))
    policy_files = [f for f in os.listdir(policies_dir) if f.endswith(".yaml") or f.endswith(".yml")]

    selected_policy = st.selectbox("Select Candidate Policy File", policy_files, index=policy_files.index("strict.yaml") if "strict.yaml" in policy_files else 0)
    candidate_path = os.path.join(policies_dir, selected_policy)

    if st.button("🚀 Replay Historical Traces", use_container_width=True):
        replay_res = replay_policy(candidate_path)
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Sessions Analyzed", replay_res["total_sessions"])
        col2.metric("Actions Re-evaluated", replay_res["total_actions"])
        col3.metric("Newly Blocked", replay_res["newly_blocked"], delta=f"+{replay_res['newly_blocked']}", delta_color="inverse")
        col4.metric("Newly Allowed", replay_res["newly_allowed"], delta=f"+{replay_res['newly_allowed']}")

        if replay_res["changed_decisions"]:
            st.write("### Decision Changes Preview")
            changes_df = pd.DataFrame(replay_res["changed_decisions"])
            st.dataframe(changes_df[["session_id", "step_index", "tool", "original_decision", "new_decision"]], use_container_width=True)
        else:
            st.info("No decision differences detected between recorded traces and candidate policy.")

    st.caption("**Limitation:** Policy replay evaluates recorded actions only. It does not simulate how an agent would behave differently after an earlier action was blocked.")


# ==============================================================================
# PAGE 6: BENCHMARK RESULTS
# ==============================================================================
elif page == "📊 Benchmark Results":
    st.markdown('<div class="main-title">Benchmark Suite Results</div>', unsafe_allow_html=True)
    st.markdown('<div class="sub-title">Performance and safety evaluations across 64 deterministic scenarios.</div>', unsafe_allow_html=True)

    bench_csv = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "benchmark", "benchmark_results.csv"))
    bench_md = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "benchmark", "benchmark_results.md"))

    if os.path.exists(bench_md):
        with open(bench_md, "r", encoding="utf-8") as f:
            st.markdown(f.read())
    else:
        st.warning("Benchmark results file not found. Please run `python benchmark/run_benchmark.py` first.")

    if os.path.exists(bench_csv):
        st.write("### Per-Scenario Comparison Table")
        df_bench = pd.read_csv(bench_csv)
        st.dataframe(df_bench, use_container_width=True)


# ==============================================================================
# PAGE 7: EMERGENCY KILL SWITCH
# ==============================================================================
elif page == "🚨 Emergency Kill Switch":
    st.markdown('<div class="main-title">Emergency Kill Switch Control</div>', unsafe_allow_html=True)
    st.markdown('<div class="sub-title">Immediate hard cutoff for all agent execution across business systems.</div>', unsafe_allow_html=True)

    is_active, active_reason = is_killswitch_active()
    
    if is_active:
        st.error(f"⚠️ **STATUS: ACTIVE** — {active_reason}")
        deactivate_reason = st.text_input("Deactivation Authorization Reason", "Operator verified system restored.")
        if st.button("🟢 DEACTIVATE GLOBAL KILL SWITCH", use_container_width=True):
            set_killswitch(active=False, scope="global", reason=deactivate_reason)
            st.success("Global kill switch deactivated. Normal execution restored.")
            st.rerun()
    else:
        st.success("🟢 **STATUS: INACTIVE (NORMAL OPERATIONS)** — All authorized tool calls proceed through standard gateway policies.")
        st.write("Activating the kill switch will immediately BLOCK all incoming agent actions and prevent pending approvals from executing.")
        
        with st.expander("🚨 Trigger Emergency Kill Switch", expanded=True):
            reason = st.text_input("Emergency Reason", "Suspected security incident or data leak.")
            confirm = st.checkbox("I confirm that I want to halt all agent activity immediately.")
            if st.button("🔴 ACTIVATE GLOBAL KILL SWITCH", disabled=not confirm, use_container_width=True):
                set_killswitch(active=True, scope="global", reason=reason)
                st.error("Global kill switch activated!")
                st.rerun()
