import streamlit as st
import streamlit.components.v1 as components
import json
import os
import sqlite3
import bisect
from datetime import datetime, time, timedelta
from collections import defaultdict
from itertools import combinations

st.set_page_config(page_title="策略回测", layout="centered")

st.markdown("""
<style>
    .block-container { padding-top: 2rem; padding-bottom: 2rem; padding-left: 1rem; padding-right: 1rem; }
    h1 { font-size: 22px !important; text-align: center; color: #e53935; margin-bottom: 0; }
    p.subtitle { text-align: center; color: #666; font-size: 13px; margin-bottom: 20px; }
    .tip-box { background: #fff8e1; color: #f57c00; padding: 8px 12px; border-radius: 6px; font-size: 12px; border-left: 4px solid #ffb300; margin-bottom: 15px; }
</style>
""", unsafe_allow_html=True)

DB_FILE = "backtest_history.db"
SETTINGS_FILE = "backtest_settings.json"

DEFAULT_SETTINGS = {
    "play_type": "三中三",
    "base_time": "22:00",
    "split_time": "22:59",
    "end_time": "23:59",
    "target_hit": 2,
    "min_miss": 120,
    "start_month": 10,
    "end_month": 11,
    "exclude_today": False,
    "pick_count": 5,
    "cold_verify_start": "19:00",
    "cold_verify_end": "23:59",
    "cold_sort_rule": "miss_max_freq_min",
    "hot_cutoff": "19:00",
    "hot_verify_end": "23:59",
    "hot_sort_rule": "freq_max_miss_min",
}

if 'settings' not in st.session_state:
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            st.session_state.settings = {**DEFAULT_SETTINGS, **loaded}
        except Exception:
            st.session_state.settings = DEFAULT_SETTINGS.copy()
    else:
        st.session_state.settings = DEFAULT_SETTINGS.copy()

s = st.session_state.settings

st.markdown("<h1>🎯 历史策略回测</h1>", unsafe_allow_html=True)
st.markdown("<p class='subtitle'>直接修改下方参数即可，点击右侧按钮开始计算</p>", unsafe_allow_html=True)

if not os.path.exists(DB_FILE):
    st.error(f"⚠️ 找不到 {DB_FILE} 文件！")
    st.stop()

# ================== 【新增：加入了特码重开号】 ==================
PLAY_TYPES = ["三中三", "二中二", "特碰", "特码冷号", "特码热号", "特码重开号"]
try:
    default_idx = PLAY_TYPES.index(s.get("play_type", "三中三"))
except ValueError:
    default_idx = 0

play_type = st.radio("🎮 选择玩法分类", PLAY_TYPES, index=default_idx, horizontal=True)
is_special = play_type in ("特码冷号", "特码热号")
is_repeat = play_type == "特码重开号"

st.markdown("---")

if not is_special and not is_repeat:
    # ================= 原有：三中三/二中二/特碰 =================
    col1, col2, col3 = st.columns(3)
    with col1:
        base_time = st.time_input("⏱️ 基准时间(算遗漏)", value=datetime.strptime(s["base_time"], "%H:%M").time())
    with col2:
        split_time = st.time_input("✂️ 前段结束(分界点)", value=datetime.strptime(s["split_time"], "%H:%M").time())
    with col3:
        end_time = st.time_input("⏹️ 后段结束(追踪终点)", value=datetime.strptime(s["end_time"], "%H:%M").time())

    col4, col5 = st.columns(2)
    with col4:
        target_hit = st.number_input("🎯 今天基准点前出现次数", min_value=1, max_value=100, value=int(s["target_hit"]), step=1)
    with col5:
        min_miss = st.number_input("📏 当前遗漏期数 ≥", min_value=1, max_value=500, value=int(s["min_miss"]), step=1)

    pick_count = 5
    cold_vs = cold_ve = hot_cutoff = hot_ve = None
    cold_sort_rule = hot_sort_rule = "miss_max_freq_min"

elif is_repeat:
    # ================= 【新增：重开号专属UI】 =================
    st.markdown("**🔄 重开号逻辑：全天计算，记录每次重号在第几期开出，以及平均间隔多少期。**")
    st.markdown("""
    <div class='tip-box'>
    💡 提示：比如今天一共开200期，期间发生了5次重号，平均遗漏就是 200 ÷ 5 = 40 期。
    </div>
    """, unsafe_allow_html=True)
    
    pick_count = 5
    cold_vs = cold_ve = hot_cutoff = hot_ve = None
    cold_sort_rule = hot_sort_rule = "miss_max_freq_min"
    base_time = split_time = end_time = None
    target_hit = min_miss = None

else:
    # ================= 原有：冷号热号 =================
    pick_count = st.number_input("📊 选取号码数量", min_value=1, max_value=20, value=int(s.get("pick_count", 5)), step=1)

    if play_type == "特码冷号":
        st.markdown("**🧊 冷号逻辑：次日指定时段验证**")
        cold_sort_opts = ["遗漏最大 + 次数最少", "遗漏最大 + 次数最多"]
        cold_rule_idx = 0 if s.get("cold_sort_rule", "miss_max_freq_min") == "miss_max_freq_min" else 1
        cold_sort_label = st.radio("排序策略", cold_sort_opts, index=cold_rule_idx, horizontal=True)
        cold_sort_rule = "miss_max_freq_min" if "次数最少" in cold_sort_label else "miss_max_freq_max"
        
        c1, c2 = st.columns(2)
        with c1:
            cold_vs = st.time_input("⏱️ 次日验证开始", value=datetime.strptime(s.get("cold_verify_start") or "19:00", "%H:%M").time())
        with c2:
            cold_ve = st.time_input("⏹️ 次日验证结束", value=datetime.strptime(s.get("cold_verify_end") or "23:59", "%H:%M").time())
        hot_cutoff = hot_ve = None
        hot_sort_rule = "freq_max_miss_min"
        base_time = split_time = end_time = None
        target_hit = min_miss = None
    else:
        st.markdown("**🔥 热号逻辑：当日指定时段验证**")
        hot_sort_opts = ["次数最多 + 遗漏最小", "次数最多 + 遗漏最大"]
        hot_rule_idx = 0 if s.get("hot_sort_rule", "freq_max_miss_min") == "freq_max_miss_min" else 1
        hot_sort_label = st.radio("排序策略", hot_sort_opts, index=hot_rule_idx, horizontal=True)
        hot_sort_rule = "freq_max_miss_min" if "遗漏最小" in hot_sort_label else "freq_max_miss_max"

        c1, c2 = st.columns(2)
        with c1:
            hot_cutoff = st.time_input("⏱️ 截止统计时间", value=datetime.strptime(s.get("hot_cutoff") or "19:00", "%H:%M").time())
        with c2:
            hot_ve = st.time_input("⏹️ 验证结束时间", value=datetime.strptime(s.get("hot_verify_end") or "23:59", "%H:%M").time())
        cold_vs = cold_ve = None
        cold_sort_rule = "miss_max_freq_min"
        base_time = split_time = end_time = None
        target_hit = min_miss = None

st.markdown("📅 **统计月份**")
m_col1, m_col2 = st.columns(2)
start_month = m_col1.selectbox("起", list(range(1, 13)), index=int(s["start_month"]) - 1, label_visibility="collapsed")
end_month = m_col2.selectbox("止", list(range(1, 13)), index=int(s["end_month"]) - 1, label_visibility="collapsed")

exclude_today = st.checkbox("☑️ 不包含今天（如果你没抓今天的数据，请勾选）", value=s.get("exclude_today", False))

st.markdown("<br>", unsafe_allow_html=True)

btn_col1, btn_col2 = st.columns(2)
with btn_col1:
    if st.button("💾 记住我当前的设置", use_container_width=True):
        new_settings = {
            "play_type": play_type, "start_month": start_month, "end_month": end_month, "exclude_today": exclude_today,
            "pick_count": pick_count if is_special else s.get("pick_count", 5), "base_time": s.get("base_time", "22:00"),
            "split_time": s.get("split_time", "22:59"), "end_time": s.get("end_time", "23:59"),
            "target_hit": s.get("target_hit", 2), "min_miss": s.get("min_miss", 120),
            "cold_verify_start": s.get("cold_verify_start", "19:00"), "cold_verify_end": s.get("cold_verify_end", "23:59"),
            "cold_sort_rule": s.get("cold_sort_rule", "miss_max_freq_min"), "hot_cutoff": s.get("hot_cutoff", "19:00"),
            "hot_verify_end": s.get("hot_verify_end", "23:59"), "hot_sort_rule": s.get("hot_sort_rule", "freq_max_miss_min"),
        }
        if not is_special and not is_repeat:
            new_settings.update({"base_time": base_time.strftime("%H:%M"), "split_time": split_time.strftime("%H:%M"), "end_time": end_time.strftime("%H:%M"), "target_hit": target_hit, "min_miss": min_miss})
        elif is_special:
            if play_type == "特码冷号":
                new_settings["cold_verify_start"] = cold_vs.strftime("%H:%M"); new_settings["cold_verify_end"] = cold_ve.strftime("%H:%M"); new_settings["cold_sort_rule"] = cold_sort_rule
            else:
                new_settings["hot_cutoff"] = hot_cutoff.strftime("%H:%M"); new_settings["hot_verify_end"] = hot_ve.strftime("%H:%M"); new_settings["hot_sort_rule"] = hot_sort_rule

        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(new_settings, f, ensure_ascii=False, indent=2)
        st.success("✅ 已记住！")

with btn_col2:
    submit = st.button("🚀 开始计算回测", use_container_width=True, type="primary")


def load_data(exclude_today):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT issue, date, nums, time FROM records")
    raw_rows = c.fetchall()
    conn.close()

    today_str = datetime.now().strftime("%Y-%m-%d")
    all_records = []
    for r in raw_rows:
        try:
            issue = int(r[0])
            date_str_raw = r[1]
            nums_str = r[2]
            time_str = r[3]

            if len(date_str_raw.split('-')[0]) == 2:
                date_str = "20" + date_str_raw
            else:
                date_str = date_str_raw

            if exclude_today and date_str == today_str:
                continue

            if "-" in time_str:
                full_time_str = time_str
            else:
                full_time_str = f"{date_str} {time_str}"

            if full_time_str.startswith("26-"):
                full_time_str = "20" + full_time_str

            dt = datetime.strptime(full_time_str, "%Y-%m-%d %H:%M:%S")

            all_records.append({
                "issue": issue,
                "date_str": date_str,
                "nums": [int(x) for x in str(nums_str).split(',') if x.strip().isdigit()],
                "dt": dt,
            })
        except Exception:
            continue

    all_records.sort(key=lambda x: x['issue'])
    days_dict = defaultdict(list)
    for r in all_records:
        days_dict[r['date_str']].append(r)
    return all_records, days_dict

# 原有逻辑
def calculate_backtest(all_records, days_dict, play_type, base_time_str, split_time_str, end_time_str, target_hit, min_miss, start_month, end_month):
    unique_dates = sorted(list(days_dict.keys()), reverse=True)
    results = []
    diagnostics = []

    for date_str in unique_dates:
        try:
            parts = date_str.split('-')
            month = int(parts[1]) if len(parts) >= 2 else int(parts[0])
        except Exception:
            continue
        if not (start_month <= month <= end_month):
            continue

        base_dt = datetime.strptime(f"{date_str} {base_time_str}", "%Y-%m-%d %H:%M")
        split_dt = datetime.strptime(f"{date_str} {split_time_str}", "%Y-%m-%d %H:%M")
        end_dt = datetime.strptime(f"{date_str} {end_time_str}", "%Y-%m-%d %H:%M")

        before_all_recs = [r for r in all_records if r['dt'] <= base_dt]
        if not before_all_recs:
            continue
        node_issue = before_all_recs[-1]['issue']

        last_issue_map = {}
        for r in before_all_recs:
            nums = r['nums']
            if len(nums) >= 7:
                front_6 = sorted(nums[:6])
                te_num = nums[6]
                combos = []
                if play_type == "三中三":
                    combos = [" ".join(f"{x:02d}" for x in c) for c in combinations(front_6, 3)]
                elif play_type == "二中二":
                    combos = [" ".join(f"{x:02d}" for x in c) for c in combinations(front_6, 2)]
                elif play_type == "特碰":
                    combos = [" ".join(sorted([f"{te_num:02d}", f"{n:02d}"])) for n in front_6]
                for combo in combos:
                    last_issue_map[combo] = r['issue']

        day_recs = days_dict[date_str]
        before_day_recs = [r for r in day_recs if r['dt'] <= base_dt]
        today_combo_count = defaultdict(int)

        for r in before_day_recs:
            nums = r['nums']
            if len(nums) >= 7:
                front_6 = sorted(nums[:6])
                te_num = nums[6]
                combos = []
                if play_type == "三中三":
                    combos = [" ".join(f"{x:02d}" for x in c) for c in combinations(front_6, 3)]
                elif play_type == "二中二":
                    combos = [" ".join(f"{x:02d}" for x in c) for c in combinations(front_6, 2)]
                elif play_type == "特碰":
                    combos = [" ".join(sorted([f"{te_num:02d}", f"{n:02d}"])) for n in front_6]
                for combo in combos:
                    today_combo_count[combo] += 1

        target_combos_dict = {}
        valid_today_combos = []
        for combo, count in today_combo_count.items():
            if count == target_hit:
                omission = node_issue - last_issue_map[combo]
                valid_today_combos.append(f"组合[{combo}] 当时的遗漏={omission}期")
                if omission >= min_miss:
                    target_combos_dict[combo] = omission

        if valid_today_combos:
            diag_text = (f"【{date_str}】共有 {len(before_day_recs)} 期数据。"
                         f"出现{target_hit}次的组合有 {len(valid_today_combos)} 个，"
                         f"例如：{', '.join(valid_today_combos[:3])}")
            diagnostics.append(diag_text)

        if not target_combos_dict:
            continue

        hit_details_list = []
        track_recs = [r for r in day_recs if base_dt < r['dt'] <= end_dt]

        for r in track_recs:
            nums = r['nums']
            r_time = r['dt']
            segment = 'front' if r_time <= split_dt else 'back'
            if len(nums) >= 7:
                front_6 = sorted(nums[:6])
                te_num = nums[6]
                combos_this_draw = []
                if play_type == "三中三":
                    combos_this_draw = [" ".join(f"{x:02d}" for x in c) for c in combinations(front_6, 3)]
                elif play_type == "二中二":
                    combos_this_draw = [" ".join(f"{x:02d}" for x in c) for c in combinations(front_6, 2)]
                elif play_type == "特碰":
                    combos_this_draw = [" ".join(sorted([f"{te_num:02d}", f"{n:02d}"])) for n in front_6]

                for combo in combos_this_draw:
                    if combo in target_combos_dict:
                        if not any(d['combo'] == combo and d['issue'] == r['issue'] for d in hit_details_list):
                            hit_details_list.append({
                                "combo": combo, "omission": target_combos_dict[combo],
                                "time": r['dt'].strftime("%H:%M:%S"), "issue": r['issue'], "segment": segment,
                            })

        hit_front_count = sum(1 for d in hit_details_list if d['segment'] == 'front')
        hit_back_count = len(hit_details_list) - hit_front_count
        hit_unique_combos = set(d['combo'] for d in hit_details_list)
        unhit_details = [{"combo": tc, "omission": om} for tc, om in target_combos_dict.items() if tc not in hit_unique_combos]

        results.append({
            "date": date_str, "total": len(target_combos_dict), "hit_total": len(hit_details_list),
            "hit_combos": len(hit_unique_combos), "hit_front": hit_front_count, "hit_back": hit_back_count,
            "hit_details": sorted(hit_details_list, key=lambda x: x['time']),
            "unhit_details": sorted(unhit_details, key=lambda x: x['combo']),
        })

    return results, diagnostics

# 原有冷热号逻辑
def calculate_special_backtest(all_records, days_dict, play_type, verify_start_str, verify_end_str, pick_count, start_month, end_month, sort_rule):
    results = []
    is_cold = (play_type == "特码冷号")
    all_te_nums = set(r['nums'][6] for r in all_records if len(r['nums']) >= 7)
    max_num = max(all_te_nums) if all_te_nums else 10
    number_range = 10 if max_num <= 10 else 49
    te_appearances = defaultdict(list)
    for r in all_records:
        if len(r['nums']) >= 7: te_appearances[r['nums'][6]].append(r['issue'])
    min_issue_global = all_records[0]['issue'] if all_records else 0
    unique_dates = sorted(list(days_dict.keys()))
    date_set = set(unique_dates)

    for pick_date in unique_dates:
        try:
            parts = pick_date.split('-'); month = int(parts[1]) if len(parts) >= 2 else int(parts[0])
        except: continue
        if not (start_month <= month <= end_month): continue

        if is_cold:
            verify_date = (datetime.strptime(pick_date, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
            if verify_date not in date_set: continue
            pick_recs = days_dict[pick_date]
        else:
            verify_date = pick_date
            cutoff_dt = datetime.strptime(f"{pick_date} {verify_start_str}", "%Y-%m-%d %H:%M")
            pick_recs = [r for r in days_dict[pick_date] if r['dt'] <= cutoff_dt]

        if not pick_recs: continue
        freq_map = defaultdict(int)
        if is_cold:
            for r in all_records:
                if len(r['nums']) >= 7: freq_map[r['nums'][6]] += 1
        else:
            for r in pick_recs:
                if len(r['nums']) >= 7: freq_map[r['nums'][6]] += 1

        base_issue = max(r['issue'] for r in pick_recs)
        all_stats = []
        for n in range(1, number_range + 1):
            appearances = te_appearances.get(n, [])
            idx = bisect.bisect_right(appearances, base_issue) - 1
            omission = base_issue - appearances[idx] if idx >= 0 else base_issue - min_issue_global + 1
            all_stats.append((n, freq_map.get(n, 0), omission))

        if is_cold:
            sorted_stats = sorted(all_stats, key=lambda x: (-x[2], x[1], x[0]) if sort_rule == "miss_max_freq_min" else (-x[2], -x[1], x[0]))
        else:
            sorted_stats = sorted(all_stats, key=lambda x: (-x[1], x[2], x[0]) if sort_rule == "freq_max_miss_min" else (-x[1], -x[2], x[0]))

        selected = sorted_stats[:pick_count]
        selected_numbers = [x[0] for x in selected]
        selected_map = {x[0]: {"freq": x[1], "omission": x[2]} for x in selected}

        verify_start_dt = datetime.strptime(f"{verify_date} {verify_start_str}", "%Y-%m-%d %H:%M")
        verify_end_dt = datetime.strptime(f"{verify_date} {verify_end_str}", "%Y-%m-%d %H:%M")
        verify_recs = [r for r in days_dict[verify_date] if verify_start_dt < r['dt'] <= verify_end_dt]

        hit_details, verify_stream, pick_stream = [], [], []
        for r in verify_recs:
            if len(r['nums']) >= 7:
                te = r['nums'][6]; is_hit = te in selected_map
                verify_stream.append({"issue": r['issue'], "time": r['dt'].strftime("%H:%M:%S"), "te": f"{te:02d}", "hit": is_hit})
                if is_hit: hit_details.append({"combo": f"{te:02d}", "omission": selected_map[te]["omission"], "time": r['dt'].strftime("%H:%M:%S"), "issue": r['issue'], "segment": "back"})
        for r in pick_recs:
            if len(r['nums']) >= 7:
                te = r['nums'][6]
                pick_stream.append({"issue": r['issue'], "time": r['dt'].strftime("%H:%M:%S"), "te": f"{te:02d}", "selected": te in selected_map})

        hit_numbers = set(d['combo'] for d in hit_details)
        freq_label = "总计" if is_cold else "截止"
        results.append({
            "date": pick_date, "verify_date": verify_date, "mode": "cold" if is_cold else "hot",
            "selected_numbers": [f"{n:02d}" for n in selected_numbers],
            "selected_info": {f"{n:02d}": f"{freq_label} {selected_map[n]['freq']}次 | 漏{selected_map[n]['omission']}期" for n in selected_numbers},
            "total": pick_count, "hit_total": len(hit_details), "hit_combos": len(hit_numbers), "hit_front": 0, "hit_back": len(hit_details),
            "hit_details": hit_details, "unhit_details": [{"combo": f"{n:02d}", "omission": selected_map[n]["omission"]} for n in selected_numbers if f"{n:02d}" not in hit_numbers],
            "verify_stream": verify_stream, "pick_stream": pick_stream, "verify_count": len(verify_stream), "pick_count_total": len(pick_stream)
        })
    return results

# 【新增：重开号专属逻辑】
def calculate_repeat_backtest(all_records, days_dict, start_month, end_month):
    results = []
    unique_dates = sorted(list(days_dict.keys()))
    
    for date_str in unique_dates:
        try:
            parts = date_str.split('-')
            month = int(parts[1]) if len(parts) >= 2 else int(parts[0])
        except:
            continue
        if not (start_month <= month <= end_month):
            continue
            
        today_recs = sorted(days_dict[date_str], key=lambda x: x['dt'])
        if not today_recs: continue
        
        total_issues = len(today_recs)
        repeats = []
        last_repeat_idx = 0
        
        for i in range(1, len(today_recs)):
            curr_te = today_recs[i]['nums'][6] if len(today_recs[i]['nums']) >= 7 else None
            prev_te = today_recs[i-1]['nums'][6] if len(today_recs[i-1]['nums']) >= 7 else None
            
            if curr_te is not None and prev_te is not None and curr_te == prev_te:
                gap = i - last_repeat_idx - 1
                repeats.append({
                    "index": i + 1, "gap": gap, "te": f"{curr_te:02d}", "time": today_recs[i]['dt'].strftime("%H:%M:%S")
                })
                last_repeat_idx = i

        repeat_count = len(repeats)
        avg_gap = int(total_issues / repeat_count) if repeat_count > 0 else 0
        
        results.append({
            "date": date_str, "total_issues": total_issues, "repeat_count": repeat_count, "avg_gap": avg_gap, "repeats": repeats
        })
        
    return results

# 原有渲染
def render_special_html(res, play_type):
    is_cold = (play_type == "特码冷号")
    is_repeat = (play_type == "特码重开号")
    json_data = json.dumps(res, ensure_ascii=False)
    accent = "#8e24aa" if is_repeat else ("#1976d2" if is_cold else "#ef6c00")
    tag_bg = "#f3e5f5" if is_repeat else ("#e3f2fd" if is_cold else "#fff3e0")
    tag_border = "#ce93d8" if is_repeat else ("#90caf9" if is_cold else "#ffcc80")

    html_content = f"""
    <!DOCTYPE html>
    <html lang="zh-CN">
    <head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        * {{ box-sizing: border-box; }}
        body {{ background: transparent; font-family: -apple-system, sans-serif; margin: 0; padding: 0; }}
        .card {{ background: #fff; border-radius: 8px; padding: 12px 15px; margin-bottom: 10px;
                 box-shadow: 0 1px 4px rgba(0,0,0,0.1); display: flex; justify-content: space-between;
                 align-items: center; border-left: 4px solid {accent}; cursor: pointer; transition: all 0.2s; }}
        .card:hover {{ box-shadow: 0 3px 8px rgba(0,0,0,0.15); transform: translateY(-1px); }}
        .card-left {{ display: flex; flex-direction: column; gap: 4px; }}
        .card-date {{ font-weight: bold; color: #333; font-size: 15px; }}
        .card-nums {{ display: flex; gap: 4px; margin-top: 2px; flex-wrap: wrap; align-items: center; }}
        .num-tag {{ font-family: monospace; font-weight: bold; font-size: 13px; padding: 2px 6px;
                    border-radius: 4px; background: {tag_bg}; color: {accent}; border: 1px solid {tag_border}; }}
        .first-hit {{ font-size: 11px; color: #e53935; margin-left: 4px; font-weight: bold; }}
        .card-stat {{ text-align: right; flex-shrink: 0; }}
        .stat-total {{ font-size: 12px; color: #666; }}
        .stat-hit {{ color: #e53935; font-weight: bold; font-size: 15px; margin-top: 4px; }}
        .stat-rate {{ font-size: 11px; color: #999; margin-top: 2px; }}

        .modal-overlay {{ position: fixed; top: 0; left: 0; width: 100%; height: 100%;
                          background: rgba(0,0,0,0.6); display: none; justify-content: center;
                          align-items: center; z-index: 999; padding: 10px; }}
        .modal-content {{ background: #fff; width: 100%; max-width: 500px; border-radius: 8px;
                          padding: 15px; max-height: 95%; display: flex; flex-direction: column; }}
        .modal-header {{ display: flex; justify-content: space-between; align-items: center;
                         border-bottom: 1px solid #eee; padding-bottom: 10px; margin-bottom: 10px;
                         position: sticky; top: 0; background: #fff; z-index: 10; }}
        .modal-title {{ font-size: 15px; font-weight: bold; color: #333; }}
        .close-btn {{ background: none; border: none; font-size: 26px; color: #999; cursor: pointer; line-height: 1; }}
        .modal-body {{ overflow-y: auto; font-size: 13px; padding-right: 4px; }}

        .section-title {{ font-weight: bold; margin: 14px 0 8px 0; color: {accent};
                          border-left: 3px solid {accent}; padding-left: 8px; font-size: 13px; }}
        .combo-grid {{ display: flex; flex-wrap: wrap; gap: 6px; }}
        .combo-item {{ background: #f5f6f8; padding: 6px 10px; border-radius: 6px;
                       display: flex; flex-direction: column; align-items: center;
                       border: 1px solid #e0e0e0; min-width: 85px; }}
        .combo-text {{ font-family: monospace; font-weight: bold; font-size: 15px; color: #333; }}
        .combo-miss {{ font-size: 11px; color: #888; margin-top: 2px; }}
        .combo-item.hit {{ background: #e8f5e9; border-color: #a5d6a7; }}
        .combo-item.hit .combo-text {{ color: #2e7d32; }}

        .summary-bar {{ display: flex; gap: 10px; padding: 10px; background: #fafbfc; border-radius: 6px; margin-bottom: 8px; }}
        .summary-item {{ flex: 1; text-align: center; }}
        .summary-item .v {{ font-size: 18px; font-weight: bold; color: #e53935; }}
        .summary-item .l {{ font-size: 11px; color: #888; margin-top: 2px; }}
        .summary-item.green .v {{ color: #2e7d32; }}

        .stream-list {{ display: flex; flex-direction: column; gap: 2px; }}
        .stream-row {{ display: flex; align-items: center; padding: 6px 8px; background: #fafbfc;
                       border-radius: 4px; font-size: 12px; gap: 8px; }}
        .stream-row.hit {{ background: #e8f5e9; border-left: 3px solid #2e7d32; }}
        .stream-row.picked {{ background: #fff8e1; border-left: 3px solid #f9a825; }}
        .stream-row .time {{ color: #888; font-family: monospace; min-width: 62px; }}
        .stream-row .issue {{ color: #bbb; font-family: monospace; min-width: 55px; font-size: 11px; }}
        .stream-row .te {{ font-family: monospace; font-weight: bold; font-size: 14px;
                           color: #333; min-width: 28px; text-align: center; }}
        .stream-row.hit .te {{ color: #2e7d32; }}
        .stream-row.picked .te {{ color: #f9a825; }}
        .stream-row .mark {{ font-size: 11px; color: #bbb; margin-left: auto; }}
        .stream-row.hit .mark {{ color: #2e7d32; font-weight: bold; }}
        .stream-row.picked .mark {{ color: #f9a825; font-weight: bold; }}
        .hit-tag {{ background: #ffebee; color: #c62828; padding: 4px 8px; border-radius: 4px; font-weight: bold; margin-left: auto; }}

        .empty-tip {{ text-align: center; padding: 15px; color: #bbb; font-size: 12px; }}
    </style>
    </head>
    <body>
    <div id="card-container"></div>
    <div class="modal-overlay" id="detailModal" onclick="closeModal(event)">
        <div class="modal-content" onclick="event.stopPropagation()">
            <div class="modal-header">
                <div class="modal-title" id="modal-title">--</div>
                <button class="close-btn" onclick="closeModal()">×</button>
            </div>
            <div class="modal-body" id="modal-body"></div>
        </div>
    </div>
    <script>
        const data = {json_data};
        const isCold = {str(is_cold).lower()};
        const isRepeat = {str(is_repeat).lower()};
        const container = document.getElementById('card-container');

        data.forEach((row, index) => {{
            const div = document.createElement('div');
            div.className = 'card';
            div.onclick = () => showDetail(index);
            
            let statHtml = '';
            if (isRepeat) {{
                statHtml = `
                    <div class="stat-hit">重开 ${{row.repeat_count}} 次</div>
                    <div class="stat-rate">平均遗漏 ${{row.avg_gap}} 期</div>
                `;
            }} else {{
                const numTags = row.selected_numbers.map(n => `<span class="num-tag">${{n}}</span>`).join('');
                let firstHitHtml = '';
                if (row.hit_details && row.hit_details.length > 0) firstHitHtml = `<span class="first-hit">首中: ${{row.hit_details[0].time}}</span>`;
                const dateLabel = isCold ? (row.date + ' → ' + row.verify_date) : row.date;
                const hitRate = row.verify_count > 0 ? (row.hit_total / row.verify_count * 100).toFixed(1) : '0.0';
                statHtml = `
                    <div class="card-date">${{dateLabel}}</div>
                    <div class="card-nums">${{numTags}} ${{firstHitHtml}}</div>
                `;
            }}
            
            const cardLeftHtml = isRepeat ? `<div class="card-date">${{row.date}}</div>` : statHtml;
            const cardStatHtml = isRepeat ? statHtml : `
                <div class="stat-total">选 ${{row.total}} 号</div>
                <div class="stat-hit">中 ${{row.hit_combos}} 号 · ${{row.hit_total}} 次</div>
                <div class="stat-rate">${{row.verify_count}} 期 · ${{(row.verify_count > 0 ? (row.hit_total / row.verify_count * 100).toFixed(1) : '0.0')}}%</div>
            `;

            div.innerHTML = `
                <div class="card-left">${{cardLeftHtml}}</div>
                <div class="card-stat">${{cardStatHtml}}</div>
            `;
            container.appendChild(div);
        }});

        function showDetail(index) {{
            const row = data[index];
            document.getElementById('modal-title').innerText = row.date + (isRepeat ? ' 重号统计' : ' 详情');

            let html = '';
            if (isRepeat) {{
                html += `<div class="summary-bar">
                    <div class="summary-item"><div class="v">${{row.total_issues}}</div><div class="l">全天总期数</div></div>
                    <div class="summary-item"><div class="v">${{row.repeat_count}}</div><div class="l">重开次数</div></div>
                    <div class="summary-item"><div class="v">${{row.avg_gap}}</div><div class="l">平均遗漏(期)</div></div>
                </div>`;
                if (row.repeats.length === 0) {{
                    html += `<div class="empty-tip">今天没有发生重号</div>`;
                }} else {{
                    html += `<div class="stream-list">`;
                    row.repeats.forEach((r) => {{
                        html += `<div class="stream-row">
                            <span class="te">${{r.te}}</span>
                            <span class="time">${{r.time}}</span>
                            <span class="hit-tag">第 ${{r.index}} 期中 (间隔 ${{r.gap}} 期)</span>
                        </div>`;
                    }});
                    html += `</div>`;
                }}
            }} else {{
                const dateLabel = isCold ? (row.date + ' 选号 → ' + row.verify_date + ' 验证') : (row.date + ' 选号 & 验证');
                html += `<div class="summary-bar">
                    <div class="summary-item"><div class="v">${{row.total}}</div><div class="l">选出号码</div></div>
                    <div class="summary-item green"><div class="v">${{row.hit_combos}}</div><div class="l">命中号码</div></div>
                    <div class="summary-item"><div class="v">${{row.hit_total}}</div><div class="l">命中次数</div></div>
                    <div class="summary-item"><div class="v">${{row.verify_count}}</div><div class="l">验证期数</div></div>
                </div>`;
                html += `<div class="section-title">📋 选出号码 (${{row.total}} 个)</div><div class="combo-grid">`;
                row.selected_numbers.forEach(n => {{
                    const isHit = row.hit_details.some(h => h.combo === n);
                    const infoLabel = row.selected_info[n] || '';
                    html += `<div class="combo-item ${{isHit ? 'hit' : ''}}">
                        <div class="combo-text">${{n}}</div>
                        <div class="combo-miss">${{infoLabel}}</div>
                    </div>`;
                }});
                html += `</div>`;
                html += `<div class="section-title">🎯 验证时段开奖流水 (${{row.verify_count}} 期 · 命中 ${{row.hit_total}} 次)</div>`;
                if (row.verify_stream.length === 0) {{
                    html += `<div class="empty-tip">验证时段内没有开奖记录</div>`;
                }} else {{
                    html += `<div class="stream-list">`;
                    row.verify_stream.forEach(r => {{
                        const cls = r.hit ? 'stream-row hit' : 'stream-row';
                        const mark = r.hit ? '✓ 命中' : '';
                        html += `<div class="${{cls}}">
                            <span class="time">${{r.time}}</span>
                            <span class="issue">#${{r.issue}}</span>
                            <span class="te">${{r.te}}</span>
                            <span class="mark">${{mark}}</span>
                        </div>`;
                    }});
                    html += `</div>`;
                }}
                if (row.pick_stream && row.pick_stream.length > 0) {{
                    html += `<div class="section-title">📥 选号日开奖流水</div><div class="stream-list">`;
                    row.pick_stream.forEach(r => {{
                        const cls = r.selected ? 'stream-row picked' : 'stream-row';
                        const mark = r.selected ? '★ 选中' : '';
                        html += `<div class="${{cls}}">
                            <span class="time">${{r.time}}</span>
                            <span class="issue">#${{r.issue}}</span>
                            <span class="te">${{r.te}}</span>
                            <span class="mark">${{mark}}</span>
                        </div>`;
                    }});
                    html += `</div>`;
                }}
            }}

            document.getElementById('modal-body').innerHTML = html;
            document.getElementById('detailModal').style.display = 'flex';
        }}

        function closeModal(e) {{
            if (e && e.target !== e.currentTarget && !e.target.classList.contains('close-btn')) return;
            document.getElementById('detailModal').style.display = 'none';
        }}
    </script>
    </body>
    </html>
    """
    return html_content


if submit:
    try:
        all_records, days_dict = load_data(exclude_today)

        if is_repeat:
            with st.spinner("正在计算全天重号..."):
                res = calculate_repeat_backtest(all_records, days_dict, start_month, end_month)
            if not res:
                st.error(f"⚠️ 没有找到符合条件的【{play_type}】数据！")
            else:
                total_hits = sum(r["repeat_count"] for r in res)
                st.success(f"✅ 计算完成！共 {len(res)} 天符合条件，累计重号开出 {total_hits} 次。点击卡片查看详情。")
                components.html(render_special_html(res, play_type), height=800, scrolling=True)

        elif is_special:
            verify_start_str = cold_vs.strftime("%H:%M") if play_type == "特码冷号" else hot_cutoff.strftime("%H:%M")
            verify_end_str = cold_ve.strftime("%H:%M") if play_type == "特码冷号" else hot_ve.strftime("%H:%M")
            sort_rule = cold_sort_rule if play_type == "特码冷号" else hot_sort_rule

            with st.spinner("正在计算特码回测..."):
                res = calculate_special_backtest(all_records, days_dict, play_type, verify_start_str, verify_end_str, pick_count, start_month, end_month, sort_rule)

            if not res:
                st.error(f"⚠️ 没有找到符合条件的【{play_type}】数据！")
            else:
                total_hits = sum(r["hit_total"] for r in res)
                total_combos_hit = sum(r["hit_combos"] for r in res)
                st.success(f"✅ 计算完成！共 {len(res)} 天符合条件，累计命中 {total_hits} 次（{total_combos_hit} 个不同号码）。点击卡片查看详情。")
                components.html(render_special_html(res, play_type), height=800, scrolling=True)

        else:
            with st.spinner("正在计算..."):
                res, diagnostics = calculate_backtest(all_records, days_dict, play_type, base_time.strftime("%H:%M"), split_time.strftime("%H:%M"), end_time.strftime("%H:%M"), target_hit, min_miss, start_month, end_month)

            if not res:
                st.error(f"⚠️ 没有找到符合条件的【{play_type}】数据！")
                if diagnostics:
                    st.warning("🔍 下面是找到的「今天出现了2次，但遗漏不达标」的组合，请检查：")
                    for diag in diagnostics[:10]: st.write(diag)
                else:
                    st.warning("🔍 警告：在你选的月份里，数据库根本没有哪一天能满足“当天出现2次”的条件！")
            else:
                st.success(f"✅ 计算完成！共有 {len(res)} 天符合条件，点击卡片查看详情。")
                json_data = json.dumps(res, ensure_ascii=False)
                html_content = f"""
                <!DOCTYPE html>
                <html lang="zh-CN">
                <head>
                <meta charset="UTF-8">
                <meta name="viewport" content="width=device-width, initial-scale=1.0">
                <style>
                    body {{ background: transparent; font-family: sans-serif; margin: 0; padding: 0; }}
                    .card {{ background: #fff; border-radius: 8px; padding: 12px 15px; margin-bottom: 10px; box-shadow: 0 1px 4px rgba(0,0,0,0.1); display: flex; justify-content: space-between; align-items: center; border-left: 4px solid #1976d2; cursor: pointer; transition: all 0.2s; }}
                    .card:hover {{ box-shadow: 0 3px 8px rgba(0,0,0,0.15); transform: translateY(-1px); }}
                    .card-left {{ display: flex; flex-direction: column; gap: 4px; }}
                    .card-date {{ font-weight: bold; color: #333; font-size: 15px; }}
                    .card-split {{ font-size: 12px; color: #555; background: #f0f2f5; padding: 2px 6px; border-radius: 4px; display: inline-block; width: fit-content; }}
                    .split-front {{ color: #d32f2f; font-weight: bold; }}
                    .split-back {{ color: #1976d2; font-weight: bold; }}
                    .card-stat {{ text-align: right; }}
                    .stat-total {{ font-size: 12px; color: #666; }}
                    .stat-total span {{ color: #1976d2; font-weight: bold; }}
                    .stat-hit {{ color: #e53935; font-weight: bold; font-size: 15px; margin-top: 4px; }}
                    .modal-overlay {{ position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: rgba(0,0,0,0.6); display: none; justify-content: center; align-items: center; z-index: 999; padding: 10px; }}
                    .modal-content {{ background: #fff; width: 100%; max-width: 500px; border-radius: 8px; padding: 15px; max-height: 95%; display: flex; flex-direction: column; }}
                    .modal-header {{ display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid #eee; padding-bottom: 10px; margin-bottom: 10px; }}
                    .modal-title {{ font-size: 16px; font-weight: bold; color: #333; }}
                    .close-btn {{ background: none; border: none; font-size: 24px; color: #999; cursor: pointer; line-height: 1; }}
                    .modal-body {{ overflow-y: auto; font-size: 14px; }}
                    .section-title {{ font-weight: bold; margin: 10px 0 8px 0; color: #1976d2; border-left: 3px solid #1976d2; padding-left: 6px; }}
                    .combo-grid {{ display: flex; flex-wrap: wrap; gap: 6px; }}
                    .combo-item {{ background: #f5f6f8; padding: 6px 10px; border-radius: 6px; display: flex; flex-direction: column; align-items: center; border: 1px solid #e0e0e0; min-width: 75px; }}
                    .combo-text {{ font-family: monospace; font-weight: bold; font-size: 14px; color: #333; }}
                    .combo-miss {{ font-size: 11px; color: #888; margin-top: 2px; }}
                    .combo-item.front {{ background: #e8f5e9; border-color: #a5d6a7; }}
                    .combo-item.front .combo-text {{ color: #2e7d32; }}
                    .combo-item.back {{ background: #e3f2fd; border-color: #90caf9; }}
                    .combo-item.back .combo-text {{ color: #1565c0; }}
                    .combo-time {{ font-size: 10px; font-weight: bold; margin-top: 4px; padding: 2px 4px; border-radius: 4px; width: 100%; text-align: center; }}
                    .combo-item.front .combo-time {{ color: #d32f2f; background: #ffebee; }}
                    .combo-item.back .combo-time {{ color: #1976d2; background: #e3f2fd; }}
                </style>
                </head>
                <body>
                <div id="card-container"></div>
                <div class="modal-overlay" id="detailModal" onclick="closeModal(event)">
                    <div class="modal-content" onclick="event.stopPropagation()">
                        <div class="modal-header">
                            <div class="modal-title" id="modal-title">--</div>
                            <button class="close-btn" onclick="closeModal()">×</button>
                        </div>
                        <div class="modal-body" id="modal-body"></div>
                    </div>
                </div>
                <script>
                    const data = {json_data};
                    const container = document.getElementById('card-container');
                    data.forEach((row, index) => {{
                        const div = document.createElement('div');
                        div.className = 'card';
                        div.onclick = () => showDetail(index);
                        div.innerHTML = `
                            <div class="card-left">
                                <div class="card-date">${{row.date}}</div>
                                <div class="card-split">前段中: <span class="split-front">${{row.hit_front}}</span> | 后段中: <span class="split-back">${{row.hit_back}}</span></div>
                            </div>
                            <div class="card-stat">
                                <div class="stat-total">筛选出: <span>${{row.total}} 组</span></div>
                                <div class="stat-hit">中 ${{row.hit_combos}} 组 (${{row.hit_total}}次)</div>
                            </div>
                        `;
                        container.appendChild(div);
                    }});
                    function showDetail(index) {{
                        const row = data[index];
                        document.getElementById('modal-title').innerText = row.date + ' 回测详情';
                        let html = '';
                        if (row.hit_details.length > 0) {{
                            html += `<div class="section-title">🎯 命中的记录 (${{row.hit_details.length}} 次)</div><div class="combo-grid">`;
                            row.hit_details.forEach(item => {{
                                let segClass = item.segment === 'front' ? 'front' : 'back';
                                html += `<div class="combo-item ${{segClass}}"><div class="combo-text">${{item.combo}}</div><div class="combo-miss">漏 ${{item.omission}}</div><div class="combo-time">${{item.time}}</div></div>`;
                            }});
                            html += `</div>`;
                        }} else {{
                            html += `<div class="section-title" style="color: #999; border-color: #999;">🎯 暂无命中组合</div>`;
                        }}
                        if (row.unhit_details.length > 0) {{
                            html += `<div class="section-title" style="margin-top: 15px;">📋 未命中的组合 (${{row.unhit_details.length}})</div><div class="combo-grid">`;
                            row.unhit_details.forEach(item => {{
                                html += `<div class="combo-item"><div class="combo-text">${{item.combo}}</div><div class="combo-miss">漏 ${{item.omission}}</div></div>`;
                            }});
                            html += `</div>`;
                        }}
                        document.getElementById('modal-body').innerHTML = html;
                        document.getElementById('detailModal').style.display = 'flex';
                    }}
                    function closeModal(e) {{
                        if (e && e.target !== e.currentTarget && !e.target.classList.contains('close-btn')) return;
                        document.getElementById('detailModal').style.display = 'none';
                    }}
                </script>
                </body>
                </html>
                """
                components.html(html_content, height=800, scrolling=True)
    except Exception as e:
        st.error(f"计算过程中发生错误: {e}")