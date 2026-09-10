"""Pipeline local : scan → agent AI (LM Studio) → project.yaml → SQLite.

Usage :
  python run_local_pipeline.py --agent PROJ_2026_003_Residence_Al_Manzah
  python run_local_pipeline.py --sync
"""
import argparse
import time
from pathlib import Path

from app.ai_agent import AIAgent
from app.config import load_config
from app.database import Database
from app.sync import SyncWorker

parser = argparse.ArgumentParser()
g = parser.add_mutually_exclusive_group(required=True)
g.add_argument("--agent", metavar="FOLDER", help="extraction LLM d'un seul dossier")
g.add_argument("--sync", action="store_true", help="synchro complète → SQLite")
args = parser.parse_args()

cfg = load_config("config.local.yaml")
agent = AIAgent(cfg.llm_agent, api_key_override=cfg.effective_api_key)
print(f"Agent activé : {agent.enabled} | base_url={agent.cfg.base_url} | "
      f"model={agent.cfg.model} | timeout={agent.cfg.timeout_seconds}s", flush=True)

if args.agent:
    folder = Path(cfg.app.projects_root_dir) / args.agent
    t0 = time.time()
    agent.process_folder(folder, use_llm=True)
    print(f"OK en {time.time() - t0:.1f}s → {folder / 'project.yaml'}", flush=True)
    print((folder / "project.yaml").read_text(encoding="utf-8"), flush=True)
else:
    db = Database(cfg.app.db_path)
    worker = SyncWorker(cfg, db, agent)
    result = worker.sync_once()
    print("Résultat de la synchronisation :", flush=True)
    for k, v in result.items():
        print(f"  {k}: {v}", flush=True)
    print("\nContenu SQLite :", flush=True)
    for row in db.list_projects():
        print(f"  [{row['id']}] {row['nom_projet']!r} | promoteur={row['promoteur']!r} | "
              f"statut={row['statut']} | gps=({row['latitude']}, {row['longitude']})", flush=True)
    db.close()
