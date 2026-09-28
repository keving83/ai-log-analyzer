"""Studio : l'analyseur de logs en interface web, avec l'IA intégrée.

Tout se fait dans le navigateur : déposer ses logs (ou la démo), lancer l'analyse,
lire les graphiques, obtenir un diagnostic IA et poser ses questions au tuteur.
Lancer : streamlit run examples/studio.py
Dépendances : pip install -r requirements-examples.txt
IA (facultatif) : clé ANTHROPIC_API_KEY, OPENAI_API_KEY ou DEEPSEEK_API_KEY en variable
d'environnement ou dans un fichier .env à la racine (jamais commité). Sans clé, tout le
reste fonctionne et les prompts restent téléchargeables pour claude.ai.
"""
import json, os, pathlib, sys, tempfile, warnings

import altair as alt
import pandas as pd
import streamlit as st

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
warnings.filterwarnings("ignore", module="openpyxl")
from analyzer import parser as P, report as REP, exports as EXP, schema as SCH, ai_diagnostic as AI
from analyzer.io_utils import read_text_any

st.set_page_config(page_title="Studio logs SEO / IA", page_icon="🤖", layout="wide")

DECISION = {"allow": "✅ Laisser passer", "limit": "🟠 Limiter", "block": "⛔ Bloquer",
            "ban_ip": "🚫 Bannir les IP", "watch": "👀 Surveiller"}
WASTE = {"with_query_params": "URL à paramètres", "status_404": "Pages 404", "status_3xx": "Redirections", "status_5xx": "Erreurs serveur",
         "static_assets": "Fichiers statiques", "admin_or_api": "Admin / API", "pagination": "Pagination"}
AI_CATS = ["ai_training", "ai_search", "ai_user_fetch", "ai_agent"]


# ---------------------------------------------------------------- analyse

def _save_upload(up, folder):
    path = pathlib.Path(folder) / pathlib.Path(up.name).name
    path.write_bytes(up.getvalue())
    return str(path)


def run_analysis(log_paths, robots=None, gsc=None, sitemap=None, crawl=None, compare=None, site=None):
    """Même pipeline que cli.py : parse → build → report.json + exports."""
    df = P.parse_files(log_paths)
    if not len(df):
        return None, P.doctor(df)
    robots_text = read_text_any(robots) if robots else None
    report, enriched = REP.build(df, robots_text, gsc, None, sitemap, crawl, compare, False, site)
    return (report, enriched), None


def _local_logs(path):
    """Fichier ou dossier saisi à la main (guillemets du copier-coller retirés). Un dossier = tous ses logs."""
    if not path: return []
    p = pathlib.Path(os.path.expanduser(path.strip().strip('"').strip("'")))
    if p.is_file(): return [str(p)]
    skip = (".xlsx", ".csv", ".txt", ".md", ".html", ".xml", ".py", ".pdf", ".png")
    return sorted(str(f) for f in p.glob("*") if f.is_file() and not f.name.lower().endswith(skip)) if p.is_dir() else []


def run_demo():
    s = ROOT / "samples"
    return run_analysis([str(s / "demo_access.log")], robots=str(s / "demo_robots.txt"),
                        gsc=str(s / "demo_gsc_pages.csv"), sitemap=str(s / "demo_sitemap.xml"),
                        crawl=str(s / "demo_screamingfrog.csv"), compare="2026-08-17")


def store(result, label):
    report, enriched = result
    st.session_state.update(report=report, label=label, chat=[], diagnostic=None,
                            hits_csv=enriched.to_csv(index=False).encode("utf-8-sig"))


# ---------------------------------------------------------------- barre latérale

with st.sidebar:
    st.header("1. Tes données")
    source = st.radio("Source", ["Logs de démonstration", "Mes fichiers de logs", "Un report.json existant"], label_visibility="collapsed")

    if source == "Logs de démonstration":
        st.caption("14 jours synthétiques avec tous les cas : faux Googlebot, rafale GPTBot, clics venant d'IA…")
        if st.button("Analyser la démo", type="primary", width="stretch"):
            with st.spinner("Analyse en cours…"):
                res, err = run_demo()
            store(res, "démo")

    elif source == "Mes fichiers de logs":
        logs = st.file_uploader("Logs serveur (.log, .gz, .zip, .json…)", accept_multiple_files=True)
        folder = st.text_input("…ou le chemin d'un fichier / dossier de logs sur ton ordinateur",
                               placeholder=r"C:\Users\moi\logs  ou  ~/logs", help="Pratique pour les gros volumes : rien n'est copié.")
        with st.expander("Options (facultatif)"):
            site = st.text_input("URL du site", placeholder="https://monsite.fr")
            robots = st.file_uploader("robots.txt", type=["txt"])
            sitemap = st.file_uploader("sitemap.xml", type=["xml"])
            gsc = st.file_uploader("Export Search Console (Pages)", type=["csv", "xlsx"])
            crawl = st.file_uploader("Export Screaming Frog / OnCrawl", type=["csv", "xlsx"])
            compare = st.date_input("Date d'une action à évaluer (avant / après)", value=None)
        local = _local_logs(folder)
        if folder and not local:
            st.error("Aucun fichier de log trouvé à cet emplacement.")
        if st.button("Analyser mes logs", type="primary", width="stretch", disabled=not (logs or local)):
            with tempfile.TemporaryDirectory() as tmp, st.spinner("Analyse en cours (1 à 2 min pour 1 M de lignes)…"):
                up = lambda f: _save_upload(f, tmp) if f else None
                if site and not site.startswith("http"): site = "https://" + site.strip("/")
                paths = [up(f) for f in logs or []] + local
                res, err = run_analysis(paths, up(robots), up(gsc), up(sitemap), up(crawl),
                                        compare.isoformat() if compare else None, site or None)
            if err:
                st.error("Aucune ligne reconnue dans ces fichiers.")
                st.code(err)
            else:
                names = [pathlib.Path(x).name for x in paths]
                store(res, ", ".join(names[:3]) + ("…" if len(names) > 3 else ""))

    else:
        up = st.file_uploader("report.json", type="json")
        if up:
            if st.session_state.get("label") != up.name:
                st.session_state.update(report=json.loads(up.getvalue().decode("utf-8-sig")), label=up.name,
                                        chat=[], diagnostic=None, hits_csv=None)

    st.divider()
    st.header("2. L'IA")
    AI._load_dotenv()
    keys = [n for n, p in AI.PROVIDERS.items() if os.environ.get(p["env"])]
    if keys:
        provider = st.selectbox("Fournisseur", keys)
        models = AI.PROVIDERS[provider]["models"]
        model = st.selectbox("Modèle", list(models), format_func=lambda m: f"{m} — {models[m]}")
    else:
        provider = model = None
        st.info("Pas de clé API détectée : le diagnostic et le chat sont désactivés, tout le reste marche.\n\n"
                "Pour les activer, crée un fichier **.env** à la racine du projet contenant par exemple :\n\n"
                "`ANTHROPIC_API_KEY=sk-ant-...`\n\npuis relance l'application.")


# ---------------------------------------------------------------- page principale

r = st.session_state.get("report")
if not r:
    st.title("🤖 Studio : qui lit ton site ?")
    st.markdown("Dépose tes logs serveur à gauche (ou lance la démo). Tu sauras quels **bots IA** lisent ton site, "
                "combien de **visites** ils te renvoient, qui se fait passer pour **Googlebot**, et quoi bloquer, "
                "limiter ou laisser passer. Ensuite, l'IA t'explique le rapport et répond à tes questions.")
    st.stop()

o, cats = r["overview"], r["categories"]
label = lambda c: cats.get(c, {}).get("label", c)
st.title("🤖 Studio : qui lit ton site ?")
st.caption(f"Source : {st.session_state['label']} · {o['period_start'][:10]} → {o['period_end'][:10]} ({o['days']:.0f} jours)")
suff = r.get("meta", {}).get("data_sufficiency", {})
if suff.get("level") and suff["level"] != "ok":
    st.warning(suff["message"])

ai_hits = sum(o["by_category"].get(c, 0) for c in AI_CATS)
spoofed = r["identity"]["summary"].get("spoofed", 0)
k = st.columns(5)
k[0].metric("Requêtes", f"{o['hits']:,}".replace(",", " "))
k[1].metric("Part des bots", f"{o['bot_share']:.0%}")
k[2].metric("Requêtes de bots IA", f"{ai_hits:,}".replace(",", " "), f"{o['ai_share']:.1%} du trafic", delta_color="off")
k[3].metric("Visites venant d'IA", f"{r['ai_referrals']['ai_clicks']:,}".replace(",", " "),
            f"{r['ai_referrals']['share_of_human_html']:.1%} des visites humaines", delta_color="off")
k[4].metric("Requêtes usurpées", f"{spoofed:,}".replace(",", " "), "faux bots" if spoofed else None,
            delta_color="inverse" if spoofed else "off")

tabs = st.tabs(["🎯 Plan d'action", "🤖 Bots IA", "🕵️ Faux bots", "↩️ Visites IA",
                "📈 Évolution", "🔎 Crawl Google", "🧠 Diagnostic IA", "💬 Questions", "⬇️ Exports"])

with tabs[0]:
    for al in [a for a in r["alerts"] if a.get("kind", "action") == "action"]:
        (st.error if al["level"] == "critical" else st.warning)(al["message"])
    for act in r.get("recommendations", {}).get("actions", []):
        with st.expander(f"**{act['rank']}. {act['title']}** · effort {act['effort']} · impact {act['impact']}", expanded=act["rank"] <= 2):
            st.markdown(f"**Pourquoi :** {act['why']}\n\n**Comment :** {act['how']}")
    fam = r.get("recommendations", {}).get("by_family", [])
    if fam:
        st.subheader("La décision bot par bot")
        df = pd.DataFrame(fam)
        df["Décision"] = df["decision"].map(DECISION).fillna(df["decision"])
        df["Catégorie"] = df["category"].map(label)
        st.dataframe(df[["family", "Catégorie", "hits", "Décision", "why", "how"]].rename(
            columns={"family": "Bot", "hits": "Requêtes", "why": "Pourquoi", "how": "Comment"}), hide_index=True, width="stretch")
    with st.expander("Bon à savoir"):
        for al in [a for a in r["alerts"] if a.get("kind") == "info"]:
            st.write("· " + al["message"])

with tabs[1]:
    actors = pd.DataFrame(r["actors"])
    ai = actors[actors["category"].isin(AI_CATS)].copy() if len(actors) else actors
    if ai.empty:
        st.info("Aucun bot IA sur la période.")
    else:
        ai["Catégorie"] = ai["category"].map(label)
        st.caption("Entraînement = collecte pour entraîner un modèle, sans visite en retour. Index de recherche = alimente "
                   "les réponses de ChatGPT, Perplexity, Claude… Fetch utilisateur = une personne a posé une question et l'IA lit ta page.")
        st.altair_chart(alt.Chart(ai.head(20)).mark_bar().encode(
            x=alt.X("hits:Q", title="Requêtes"), y=alt.Y("family:N", sort="-x", title=None),
            color=alt.Color("Catégorie:N", legend=alt.Legend(orient="bottom")),
            tooltip=["family", "operator", "Catégorie", "hits", "unique_urls"]), width="stretch")
        ai["robots.txt lu"] = ai["fetched_robots_txt"].map({True: "oui", False: "non"})
        st.dataframe(ai[["family", "operator", "Catégorie", "hits", "hits_per_day", "unique_urls", "robots.txt lu",
                         "max_hits_per_minute", "spoofed_share"]].rename(columns={
            "family": "Bot", "operator": "Opérateur", "hits": "Requêtes", "hits_per_day": "Par jour", "unique_urls": "Pages",
            "max_hits_per_minute": "Pic / min", "spoofed_share": "Part usurpée"}), hide_index=True, width="stretch",
            column_config={"Part usurpée": st.column_config.ProgressColumn(format="percent", min_value=0, max_value=1)})
    cf = r.get("control_files", {})
    if cf:
        with st.expander("Qui lit robots.txt, llms.txt, ai.txt ?"):
            st.json(cf, expanded=False)

with tabs[2]:
    st.caption(r["explain"].get("identity", ""))
    ids = r["identity"]["summary"]
    c1, c2 = st.columns([1, 2])
    with c1:
        for key, name in [("verified", "✅ Vérifiés"), ("spoofed", "🚫 Usurpés"), ("unverified", "❔ Non vérifiables"), ("n/a", "➖ Sans méthode")]:
            st.metric(name, f"{ids.get(key, 0):,}".replace(",", " "), help=r["identity"]["labels"].get(key))
    with c2:
        sp = pd.DataFrame(r["identity"]["spoofed_ips"])
        if sp.empty:
            st.success("Aucune usurpation détectée.")
        else:
            st.markdown("**IP à bannir** (jamais l'User-Agent : tu bloquerais aussi le vrai bot)")
            st.dataframe(sp.rename(columns={"ip": "IP", "family": "Se fait passer pour", "hits": "Requêtes"}), hide_index=True, width="stretch")
            st.download_button("Télécharger la liste d'IP (.txt)", "\n".join(sp["ip"].unique()), "ip_a_bannir.txt")

with tabs[3]:
    ref = r["ai_referrals"]
    st.caption("Visites humaines arrivées en cliquant un lien dans ChatGPT, Perplexity, Claude, Copilot, Gemini…")
    c1, c2 = st.columns(2)
    if ref["by_source"]:
        c1.markdown("**D'où viennent ces visites**")
        c1.altair_chart(alt.Chart(pd.DataFrame(ref["by_source"].items(), columns=["Source", "Visites"])).mark_bar().encode(
            x="Visites:Q", y=alt.Y("Source:N", sort="-x", title=None), tooltip=["Source", "Visites"]), width="stretch")
    else:
        c1.info("Aucune visite venant d'une IA sur la période.")
    if ref["by_page"]:
        c2.markdown("**Pages qui reçoivent ces visites**")
        c2.dataframe(pd.DataFrame(ref["by_page"].items(), columns=["Page", "Visites"]), hide_index=True, width="stretch")
    if ref.get("fetched_never_clicked_top"):
        st.markdown("**Pages lues par les IA mais jamais cliquées**")
        st.dataframe(pd.DataFrame(ref["fetched_never_clicked_top"]), hide_index=True, width="stretch")

with tabs[4]:
    daily = pd.DataFrame(r["timeline"]["daily"]).T.fillna(0)
    if daily.empty:
        st.info("Pas de série temporelle.")
    else:
        daily.index.name = "Jour"
        long = daily.reset_index().melt("Jour", var_name="cat", value_name="Requêtes")
        long["Catégorie"] = long["cat"].map(label)
        only_bots = st.toggle("Masquer les humains", value=True)
        if only_bots: long = long[long["cat"] != "human"]
        st.altair_chart(alt.Chart(long).mark_area().encode(
            x=alt.X("Jour:T", title=None), y=alt.Y("Requêtes:Q", stack=True), color=alt.Color("Catégorie:N", legend=alt.Legend(orient="bottom", columns=4)),
            tooltip=["Jour:T", "Catégorie", "Requêtes"]), width="stretch")
    for f in r.get("compare", {}).get("findings", []):
        icon = {"effect": "✅", "warning": "⚠️", "note": "ℹ️", "caveat": "🔸"}.get(f["kind"], "·")
        st.markdown(f"{icon} **{f['title']}** : {f['text']}")

with tabs[5]:
    engines = [e for e in r["crawl_budget"] if not e.startswith("_")]
    if not engines:
        st.info("Aucun passage de Googlebot ou Bingbot sur la période.")
    else:
        e = st.selectbox("Robot", engines)
        cb = r["crawl_budget"][e]
        c1, c2 = st.columns(2)
        c1.metric("Crawl gaspillé", f"{cb['waste_share']:.0%}", help="Paramètres, 404, 5xx, admin, pagination")
        c2.metric("Délai médian de recrawl", f"{cb['recrawl_median_days']} j")
        w = pd.DataFrame([(WASTE.get(k, k), v) for k, v in cb["waste"].items() if isinstance(v, (int, float)) and v], columns=["Type", "Requêtes"])
        if len(w):
            st.altair_chart(alt.Chart(w).mark_bar().encode(x="Requêtes:Q", y=alt.Y("Type:N", sort="-x", title=None)), width="stretch")
        st.markdown("**Par section du site**")
        st.dataframe(pd.DataFrame(cb["by_segment"]).rename(columns={"seg": "Section", "hits": "Requêtes", "urls": "Pages", "err": "Erreurs"}),
                     hide_index=True, width="stretch")

with tabs[6]:
    st.markdown("L'IA lit ton rapport complet et rédige un diagnostic en français : priorités, explications, actions.")
    if not provider:
        st.info("Ajoute une clé API (voir la barre latérale) pour activer le diagnostic. "
                "Sinon : télécharge le prompt ci-dessous et colle-le dans claude.ai avec ton report.json.")
        st.download_button("Télécharger le prompt de diagnostic", AI.load_prompt("diagnostic"), "prompt_diagnostic.md")
    elif st.button("Lancer le diagnostic", type="primary"):
        slim, removed = AI.slim_report(r)
        note = f"\n\n(Sections retirées du rapport pour tenir dans la limite : {', '.join(removed)})" if removed else ""
        try:
            llm = AI.LLM(provider, model)
            text = st.write_stream(llm.stream([{"role": "user", "content": f"{AI.load_prompt('diagnostic')}{note}\n\nVoici report.json :\n```json\n{slim}\n```"}],
                                              max_tokens=AI.DIAG_MAX_TOKENS))
            st.session_state["diagnostic"] = text
            if llm.truncated: st.warning("Réponse coupée par la limite de longueur : essaie un modèle plus concis.")
        except Exception as ex:
            st.error(f"Échec de l'appel à l'IA : {ex}")
    elif st.session_state.get("diagnostic"):
        st.markdown(st.session_state["diagnostic"])
    if st.session_state.get("diagnostic"):
        st.download_button("Télécharger le diagnostic (.md)", st.session_state["diagnostic"], "diagnostic.md")

with tabs[7]:
    st.markdown("Pose tes questions sur ton rapport : *« Dois-je bloquer GPTBot ? »*, *« Pourquoi Googlebot usurpé ? »*, "
                "*« Explique-moi le crawl budget comme à un DG »*…")
    if not provider:
        st.info("Ajoute une clé API (voir la barre latérale) pour activer le chat.")
    else:
        for m in st.session_state["chat"]:
            st.chat_message(m["role"]).markdown(m["content"])
        if q := st.chat_input("Ta question…"):
            st.chat_message("user").markdown(q)
            st.session_state["chat"].append({"role": "user", "content": q})
            slim, removed = AI.slim_report(r)
            system = AI.load_prompt("tuteur") + "\n\nVoici report.json :\n```json\n" + slim + "\n```"
            if removed: system += f"\n(Sections retirées pour tenir dans la limite : {', '.join(removed)})"
            try:
                llm = AI.LLM(provider, model)
                with st.chat_message("assistant"):
                    answer = st.write_stream(llm.stream(st.session_state["chat"], system=system, max_tokens=4000, cache_system=True))
                st.session_state["chat"].append({"role": "assistant", "content": answer})
            except Exception as ex:
                st.session_state["chat"].pop()
                st.error(f"Échec de l'appel à l'IA : {ex}")

with tabs[8]:
    st.markdown("Emporte ton rapport : le HTML s'ouvre partout, le JSON sert à construire ton propre tableau de bord avec Claude.")
    c = st.columns(4)
    c[0].download_button("report.html", EXP.build_report_html(r), "report.html", width="stretch")
    c[1].download_button("summary.md", EXP.build_summary_md(r), "summary.md", width="stretch")
    c[2].download_button("report.json", json.dumps(r, ensure_ascii=False, indent=1), "report.json", width="stretch")
    if st.session_state.get("hits_csv"):
        c[3].download_button("hits.csv (Excel)", st.session_state["hits_csv"], "hits.csv", width="stretch")
    st.download_button("report_schema.json (structure allégée à donner à Claude)",
                       json.dumps(SCH.build(r), ensure_ascii=False, indent=1), "report_schema.json")
