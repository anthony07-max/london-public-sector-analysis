# %% [markdown]
# # Crime and Deprivation Across London's Boroughs, with a Focus on Newham
#
# **Policy questions**
# 1. How has recorded crime in London changed over the last six years?
# 2. Which boroughs have the highest crime rates once population is taken into account?
# 3. How closely does crime track deprivation, and which crimes are most linked to it?
# 4. How does Newham compare: is its crime higher or lower than its level of deprivation would predict?
#
# **Data (all open):** Metropolitan Police recorded crime by borough and month (Oct 2020 to Sep 2026), English Indices of
# Deprivation 2025 (local authority summaries), and ONS mid-2025 population estimates.

# %%
import json
from pathlib import Path

import matplotlib.pyplot as plt
import matplotlib.ticker as mtick
import numpy as np
import pandas as pd
import seaborn as sns
import statsmodels.api as sm
from scipy import stats

ROOT = Path.cwd().parent if Path.cwd().name == "notebooks" else Path.cwd()
RAW, FIG, RES = ROOT / "data" / "raw", ROOT / "figures", ROOT / "results"
FIG.mkdir(exist_ok=True); RES.mkdir(exist_ok=True)
sns.set_theme(style="whitegrid", context="notebook")
NAVY, ORANGE, GREY, NEWHAM = "#1f4e79", "#e07b39", "#b8c2cc", "#c0392b"
out = {}

def savefig(fig, name):
    fig.tight_layout(); fig.savefig(FIG / name, dpi=150, bbox_inches="tight"); plt.show()

# %% [markdown]
# ## 1. Load and clean the crime data
# * The two MPS files (historical: Oct 2020 to Sep 2024; recent: Oct 2024 to Sep 2026) use the same Home Office
#   categories and do not overlap, so they are stacked into one long table.
# * Rows for "Aviation Policing" (Heathrow) and "Unknown" location are dropped: they are not boroughs.
# * **Fraud is excluded** (both "Fraud and Forgery" and "NFIB Fraud"): most fraud is reported to Action Fraud
#   nationally, so borough counts say little about local crime.
# * The City of London has its own police force and is not in this data.

# %%
def melt(path):
    d = pd.read_csv(path)
    return d.melt(id_vars=["Group", "SubGroup", "BOCU"], var_name="month", value_name="crimes")

raw = pd.concat([melt(RAW / "mps_borough_crime_historical.csv"), melt(RAW / "mps_borough_crime_24m.csv")], ignore_index=True)
raw["month"] = pd.to_datetime(raw["month"], format="%Y%m")
raw = raw.rename(columns={"Group": "major", "SubGroup": "minor", "BOCU": "borough"})
print("Raw rows:", len(raw), "| months:", raw["month"].min().date(), "to", raw["month"].max().date(), "|", raw["month"].nunique(), "months")
print("Duplicate borough x category x month rows:", raw.duplicated(["major", "minor", "borough", "month"]).sum())
print("Negative counts:", (raw["crimes"] < 0).sum(), "| missing:", raw["crimes"].isna().sum())

excluded = raw[raw["borough"].isin(["Aviation Policing", "Unknown"]) | raw["major"].isin(["FRAUD AND FORGERY", "NFIB FRAUD"])]
print(f"Excluded {excluded['crimes'].sum():,} crimes ({excluded['crimes'].sum() / raw['crimes'].sum():.1%}): non-borough rows and fraud")
crime = raw.drop(excluded.index).copy()
crime["major"] = crime["major"].str.title().str.replace("Against The Person", "Against the Person").str.replace(" And ", " and ").str.replace(" Of ", " of ")
print("Boroughs:", crime["borough"].nunique())
out["months"] = [str(crime["month"].min().date()), str(crime["month"].max().date())]
out["excluded_share_pct"] = round(100 * excluded["crimes"].sum() / raw["crimes"].sum(), 1)

# %% [markdown]
# ## 2. London-wide trend
# Note: the first 12 months (Oct 2020 to Sep 2021) include COVID-19 lockdowns, when theft and robbery fell sharply.
# Long-run changes are therefore inflated by a low base; the year-on-year comparison is the fairer measure.

# %%
monthly = crime.groupby("month")["crimes"].sum()
by_major = crime.groupby(["month", "major"])["crimes"].sum().unstack()
roll = monthly.rolling(12).sum()

def fy(m):  # 12-month periods ending September
    return f"Oct {m.year - 1 if m.month <= 9 else m.year} to Sep {m.year if m.month <= 9 else m.year + 1}"
crime["period"] = crime["month"].apply(fy)
periods = crime.groupby("period")["crimes"].sum()
print(periods)
out["period_totals"] = periods.to_dict()
first, last = periods.iloc[0], periods.iloc[-1]
prev = periods.iloc[-2]
out["change_6y_pct"] = round(100 * (last / first - 1), 1)
out["change_1y_pct"] = round(100 * (last / prev - 1), 1)

fig, axes = plt.subplots(1, 2, figsize=(16, 5))
axes[0].plot(monthly.index, monthly.values, color=GREY, lw=1, label="monthly")
axes[0].plot(roll.index, roll.values / 12, color=NAVY, lw=2.5, label="12-month average")
axes[0].set(title="Recorded crime in London (excluding fraud), per month", ylabel="crimes")
axes[0].yaxis.set_major_formatter(mtick.FuncFormatter(lambda x, _: f"{x/1000:.0f}k"))
axes[0].legend()
top = by_major.sum().sort_values(ascending=False).head(6).index
idx = by_major[top].rolling(12).sum().dropna()
idx = idx / idx.iloc[0] * 100
for c in top:
    axes[1].plot(idx.index, idx[c], lw=2, label=c)
axes[1].axhline(100, color="k", lw=0.8)
axes[1].set(title="12-month rolling total, indexed (12 months to Sep 2021 = 100;\nthat base year includes COVID-19 lockdowns)", ylabel="index")
axes[1].legend(fontsize=8)
savefig(fig, "01_london_trend.png")
last12 = crime[crime["month"] > crime["month"].max() - pd.DateOffset(months=12)]
first12 = crime[crime["month"] < crime["month"].min() + pd.DateOffset(months=12)]
chg = (last12.groupby("major")["crimes"].sum() / first12.groupby("major")["crimes"].sum() - 1) * 100
out["category_change_6y_pct"] = chg.round(1).sort_values().to_dict()
chg.round(1).sort_values()

# %% [markdown]
# ## 3. Join population and deprivation
# Rates use **ONS mid-2025 population** (the middle of the latest 12-month window). Deprivation uses the **IoD 2025
# Income domain average score** (share of residents in income deprivation). The overall IMD includes a crime domain,
# so correlating crime with the overall IMD would be partly circular; the income domain avoids that.

# %%
pop = pd.read_csv(RAW / "ons_population_mid2025_lad.csv").rename(columns={"GEOGRAPHY_NAME": "borough", "GEOGRAPHY_CODE": "code", "OBS_VALUE": "population"})
iod = pd.ExcelFile(RAW / "iod2025_file10_lad_summaries_v2.xlsx")
def iod_sheet(name, cols):
    d = pd.read_excel(iod, name)
    d.columns = [c.strip() for c in d.columns]
    return d[["Local Authority District code (2024)"] + cols].rename(columns={"Local Authority District code (2024)": "code"})
imd = iod_sheet("IMD", ["IMD - Average score", "IMD - Rank of average score", "IMD - Proportion of LSOAs in most deprived 10% nationally"])
inc = iod_sheet("Income", ["Income - Average score", "Income - Rank of average score"])
dep = imd.merge(inc, on="code")
dep.columns = ["code", "imd_score", "imd_rank", "imd_share_lsoas_top10pct", "income_score", "income_rank"]

boroughs = sorted(crime["borough"].unique())
pop_l = pop[pop["borough"].isin(boroughs)]
missing = set(boroughs) - set(pop_l["borough"])
print("Boroughs without a population match:", missing or "none")
b = pop_l.merge(dep, on="code", how="left")
assert len(b) == 32 and b.isna().sum().sum() == 0
n_lads = len(pd.read_excel(iod, "IMD"))
print(f"IMD ranks are out of {n_lads} local authority districts in England (1 = most deprived)")
out["n_lads_england"] = n_lads

# Latest 12 months: Oct 2025 to Sep 2026
rates = last12.pivot_table(index="borough", columns="major", values="crimes", aggfunc="sum")
rates["Total"] = rates.sum(axis=1)
b = b.set_index("borough").join(rates)
rate_cols = list(rates.columns)
for c in rate_cols:
    b[f"rate_{c}"] = b[c] / b["population"] * 1000
london_rate = {c: b[c].sum() / b["population"].sum() * 1000 for c in rate_cols}
b.round(3).to_csv(RES / "borough_table_latest12m.csv")
print(f"London total crime rate (latest 12 months, ex fraud): {london_rate['Total']:.1f} per 1,000")
out["london_rate_total"] = round(london_rate["Total"], 1)
out["latest_12m_total"] = int(b["Total"].sum())
b[["population", "income_score", "imd_rank", "Total", "rate_Total"]].sort_values("rate_Total", ascending=False).round(1)

# %% [markdown]
# ## 4. Which boroughs have the highest crime rates?

# %%
s = b["rate_Total"].sort_values()
fig, ax = plt.subplots(figsize=(10, 10))
colors = [NEWHAM if i == "Newham" else (ORANGE if i in ("Westminster", "Camden", "Kensington and Chelsea") else NAVY) for i in s.index]
ax.barh(s.index, s.values, color=colors)
ax.axvline(london_rate["Total"], color="k", ls="--", lw=1)
ax.text(london_rate["Total"] + 1, 0, f"London {london_rate['Total']:.0f}", fontsize=9)
for i, v in enumerate(s.values):
    ax.text(v + 1, i, f"{v:.0f}", va="center", fontsize=8)
ax.set(title="Recorded crimes per 1,000 residents, Oct 2025 to Sep 2026 (excluding fraud)\norange = central boroughs with large visitor and worker populations, red = Newham", xlabel="crimes per 1,000 residents")
ax.title.set_fontsize(11)
savefig(fig, "02_borough_crime_rates.png")
rank_total = b["rate_Total"].rank(ascending=False).astype(int)
out["westminster_rate"] = round(b.loc["Westminster", "rate_Total"], 1)
out["lowest_borough"] = [s.index[0], round(s.iloc[0], 1)]
out["newham_rate_total"] = round(b.loc["Newham", "rate_Total"], 1)
out["newham_rank_total"] = int(rank_total["Newham"])

# %% [markdown]
# ## 5. How closely does crime track deprivation?
# Correlation (Pearson r on rates, Spearman rho on ranks) between income deprivation and each crime type across the
# 32 boroughs. Westminster is an extreme outlier for theft (residents are a small share of the people present), so
# results are shown with and without the three central boroughs (Westminster, Camden, Kensington and Chelsea).

# %%
central = ["Westminster", "Camden", "Kensington and Chelsea"]
rows = []
for c in rate_cols:
    x, y = b["income_score"], b[f"rate_{c}"]
    r, p = stats.pearsonr(x, y)
    rho, prho = stats.spearmanr(x, y)
    xo, yo = x.drop(central), y.drop(central)
    r2, p2 = stats.pearsonr(xo, yo)
    rows.append({"crime_type": c, "pearson_r": r, "p": p, "spearman_rho": rho, "p_spearman": prho,
                 "pearson_r_excl_central": r2, "p_excl_central": p2})
corr = pd.DataFrame(rows).sort_values("pearson_r_excl_central", ascending=False)
corr.round(4).to_csv(RES / "deprivation_correlations.csv", index=False)
out["correlations"] = corr.round(3).to_dict("records")
corr.round(3)

# %%
fig, axes = plt.subplots(1, 2, figsize=(16, 6.5))
for ax, col, title in [(axes[0], "rate_Violence Against the Person", "Violence against the person"),
                       (axes[1], "rate_Theft", "Theft")]:
    x, y = b["income_score"] * 100, b[col]
    ax.scatter(x, y, color=NAVY, s=40)
    for name in b.index:
        if name in ["Newham", "Westminster", "Camden", "Tower Hamlets", "Barking and Dagenham", "Richmond upon Thames",
                    "Kensington and Chelsea", "Hackney", "Lambeth", "Croydon", "Enfield", "Sutton", "Islington", "Haringey"]:
            ax.annotate(name, (x[name], y[name]), fontsize=8, xytext=(4, -12) if name == "Newham" else (4, 3), textcoords="offset points",
                        color=NEWHAM if name == "Newham" else "black", weight="bold" if name == "Newham" else None)
    ax.scatter(x["Newham"], y["Newham"], color=NEWHAM, s=90, zorder=3)
    keep = ~b.index.isin(central)
    m, c0 = np.polyfit(x[keep], y[keep], 1)
    xs = np.linspace(x.min(), x.max(), 50)
    ax.plot(xs, m * xs + c0, color=ORANGE, lw=1.5, label="fit excluding central boroughs")
    r = corr.set_index("crime_type").loc[col.replace("rate_", "")]
    ax.set(title=f"{title}: r = {r.pearson_r:.2f} (all), {r.pearson_r_excl_central:.2f} (excluding central)",
           xlabel="Income deprivation (% of residents, IoD 2025)", ylabel="crimes per 1,000 residents")
    ax.legend(loc="upper left")
savefig(fig, "03_deprivation_vs_crime.png")

# %% [markdown]
# ## 6. Newham in focus
# ### 6.1 Deprivation profile

# %%
nh = b.loc["Newham"]
inc_rank_london = b["income_score"].rank(ascending=False).astype(int)
imd_rank_london = b["imd_score"].rank(ascending=False).astype(int)
out["newham"] = {
    "population": int(nh["population"]), "imd_rank_england": int(nh["imd_rank"]), "imd_rank_london": int(imd_rank_london["Newham"]),
    "income_score_pct": round(nh["income_score"] * 100, 1), "income_rank_england": int(nh["income_rank"]),
    "income_rank_london": int(inc_rank_london["Newham"]),
    "share_lsoas_most_deprived_10pct": round(nh["imd_share_lsoas_top10pct"] * 100, 1),
    "london_income_score_median_pct": round(b["income_score"].median() * 100, 1),
}
out["newham"]

# %% [markdown]
# ### 6.2 Crime by type: Newham vs London, and vs what its deprivation predicts
# For each crime type, a simple linear regression of borough rate on income deprivation is fitted on the 29
# non-central boroughs. Newham's **residual** shows whether its rate is above or below what a borough with its level
# of deprivation would typically have.

# %%
rows = []
keep = ~b.index.isin(central)
for c in rate_cols:
    X = sm.add_constant(b.loc[keep, "income_score"])
    y = b.loc[keep, f"rate_{c}"]
    model = sm.OLS(y, X).fit()
    pred_nh = model.predict(pd.DataFrame({"const": [1.0], "income_score": [nh["income_score"]]}))[0]
    resid_sd = np.sqrt(model.scale)
    rank = int(b[f"rate_{c}"].rank(ascending=False)["Newham"])
    rows.append({"crime_type": c, "newham_crimes": int(nh[c]), "newham_rate": nh[f"rate_{c}"], "london_rate": london_rate[c],
                 "newham_vs_london_pct": (nh[f"rate_{c}"] / london_rate[c] - 1) * 100, "newham_rank_of_32": rank,
                 "predicted_from_deprivation": pred_nh, "residual": nh[f"rate_{c}"] - pred_nh,
                 "residual_z": (nh[f"rate_{c}"] - pred_nh) / resid_sd, "r2": model.rsquared})
newham = pd.DataFrame(rows).sort_values("newham_crimes", ascending=False)
newham.round(3).to_csv(RES / "newham_profile.csv", index=False)
out["newham_by_type"] = newham.round(2).to_dict("records")
newham.round(2)

# %%
nplot = newham[newham["crime_type"] != "Total"].sort_values("newham_vs_london_pct")
fig, axes = plt.subplots(1, 2, figsize=(16, 6))
axes[0].barh(nplot["crime_type"], nplot["newham_vs_london_pct"], color=[NEWHAM if v > 0 else NAVY for v in nplot["newham_vs_london_pct"]])
axes[0].axvline(0, color="k", lw=1)
axes[0].xaxis.set_major_formatter(mtick.PercentFormatter())
axes[0].set(title="Newham's crime rate vs the London rate, by type\n(Oct 2025 to Sep 2026)", xlabel="% above (+) or below (-) London")
nz = newham[newham["crime_type"] != "Total"].sort_values("residual_z")
axes[1].barh(nz["crime_type"], nz["residual_z"], color=[NEWHAM if v > 0 else NAVY for v in nz["residual_z"]])
axes[1].axvline(0, color="k", lw=1)
for v in (-1, 1): axes[1].axvline(v, color=GREY, ls=":", lw=1)
axes[1].set(title="Newham vs the rate predicted by its income deprivation\n(standardised residual; dotted lines = 1 standard deviation)", xlabel="standardised residual")
savefig(fig, "04_newham_vs_london.png")

# %% [markdown]
# ### 6.3 Trend: Newham vs the rest of London

# %%
nh_m = crime[crime["borough"] == "Newham"].groupby("month")["crimes"].sum()
rest_m = crime[crime["borough"] != "Newham"].groupby("month")["crimes"].sum()
nh_idx = nh_m.rolling(12).sum().dropna(); rest_idx = rest_m.rolling(12).sum().dropna()
nh_idx, rest_idx = nh_idx / nh_idx.iloc[0] * 100, rest_idx / rest_idx.iloc[0] * 100
prev12 = crime[(crime["month"] <= crime["month"].max() - pd.DateOffset(months=12)) & (crime["month"] > crime["month"].max() - pd.DateOffset(months=24))]
def yoy(df_now, df_prev, where):
    a = df_now[where(df_now)].groupby("major")["crimes"].sum(); p = df_prev[where(df_prev)].groupby("major")["crimes"].sum()
    t = pd.DataFrame({"latest_12m": a, "previous_12m": p}); t.loc["Total"] = t.sum()
    t["change_pct"] = (t["latest_12m"] / t["previous_12m"] - 1) * 100
    return t
y_nh = yoy(last12, prev12, lambda d: d["borough"] == "Newham")
y_ld = yoy(last12, prev12, lambda d: d["borough"] != "Newham")
yy = y_nh.join(y_ld, lsuffix="_newham", rsuffix="_rest_of_london").sort_values("latest_12m_newham", ascending=False)
yy.round(1).to_csv(RES / "newham_year_on_year.csv")
out["newham_yoy"] = yy.round(1).reset_index().rename(columns={"index": "major", "major": "major"}).to_dict("records")
out["newham_change_6y_pct"] = round(float(nh_idx.iloc[-1] - 100), 1)
out["rest_change_6y_pct"] = round(float(rest_idx.iloc[-1] - 100), 1)

fig, axes = plt.subplots(1, 2, figsize=(16, 5))
axes[0].plot(nh_idx.index, nh_idx, color=NEWHAM, lw=2.5, label="Newham")
axes[0].plot(rest_idx.index, rest_idx, color=NAVY, lw=2.5, label="Rest of London")
axes[0].axhline(100, color="k", lw=0.8)
axes[0].set(title="12-month rolling crime total, indexed\n(12 months to Sep 2021 = 100; base year includes lockdowns)", ylabel="index"); axes[0].legend()
t = yy.drop("Total").head(8)
xpos = np.arange(len(t))
axes[1].bar(xpos - 0.2, t["change_pct_newham"], 0.4, color=NEWHAM, label="Newham")
axes[1].bar(xpos + 0.2, t["change_pct_rest_of_london"], 0.4, color=NAVY, label="Rest of London")
axes[1].set_xticks(xpos, t.index, rotation=30, ha="right", fontsize=8)
axes[1].axhline(0, color="k", lw=0.8); axes[1].yaxis.set_major_formatter(mtick.PercentFormatter())
axes[1].set(title="Change: Oct 2025 to Sep 2026 vs the previous 12 months"); axes[1].legend()
savefig(fig, "05_newham_trend.png")
yy.round(1)

# %% [markdown]
# ### 6.4 Which specific offences drive Newham's numbers?

# %%
minor = last12[last12["borough"] == "Newham"].groupby(["major", "minor"])["crimes"].sum().sort_values(ascending=False)
minor_ld = last12.groupby(["major", "minor"])["crimes"].sum()
pop_nh, pop_ld = nh["population"], b["population"].sum()
mt = pd.DataFrame({"newham_crimes": minor, "newham_rate": minor / pop_nh * 1000, "london_rate": minor_ld / pop_ld * 1000})
mt["vs_london_pct"] = (mt["newham_rate"] / mt["london_rate"] - 1) * 100
mt = mt.sort_values("newham_crimes", ascending=False)
mt.round(2).to_csv(RES / "newham_offence_detail.csv")
out["newham_top_offences"] = mt.head(10).round(2).reset_index().to_dict("records")
mt.head(12).round(2)

# %%
with open(RES / "summary_stats.json", "w") as f:
    json.dump(out, f, indent=2, default=lambda o: o.item() if hasattr(o, "item") else str(o))
print(json.dumps(out, indent=2, default=lambda o: o.item() if hasattr(o, "item") else str(o)))
