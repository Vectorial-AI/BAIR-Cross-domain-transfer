"""
fix_selfmap.py
--------------
Fixes a prompt artifact in topic_cluster_map.json where some labels
were mapped to the literal string "self-map" instead of to themselves.

Wherever the canonical value is exactly "self-map", replaces it with
the raw label key (the correct self-map behaviour).

Also fixes the updated canonical vocabulary (canonical_topics_v1.json)
by removing "self-map" as a vocabulary entry if present.

Usage:
    python fix_selfmap.py
    python fix_selfmap.py --config config_vocab.yaml

Author: Ananth / Vectorial AI -- June 2026
"""

import argparse
import json
import os
from pathlib import Path

import yaml

DEFAULTS = {
    "map_output":   "topic_cluster_map.json",
    "vocab_output": "canonical_topics_v1.json",
}

def load_config(path: str) -> dict:
    cfg = DEFAULTS.copy()
    config_path = Path(os.path.dirname(os.path.abspath(__file__))) / path
    if config_path.exists():
        with open(config_path, encoding="utf-8") as f:
            overrides = yaml.safe_load(f) or {}
        cfg.update(overrides)
    return cfg

def resolve(here: str, p: str) -> str:
    return os.path.join(here, p)

def fix_cluster_map(map_path: str) -> dict:
    with open(map_path, encoding="utf-8") as f:
        cluster_map = json.load(f)

    total_fixed = 0
    domain_counts = {}

    for domain, domain_map in cluster_map.items():
        fixed = 0
        for raw_label, canonical in domain_map.items():
            if canonical == "self-map":
                domain_map[raw_label] = raw_label
                fixed += 1
        domain_counts[domain] = fixed
        total_fixed += fixed

    return cluster_map, total_fixed, domain_counts

def fix_vocab(vocab_path: str) -> dict:
    with open(vocab_path, encoding="utf-8") as f:
        vocab = json.load(f)

    removed = {}
    for domain, topics in vocab.items():
        before = len(topics)
        vocab[domain] = [t for t in topics if t != "self-map"]
        after = len(vocab[domain])
        if before != after:
            removed[domain] = before - after

    return vocab, removed

def run(cfg_path: str = "config_vocab.yaml"):
    cfg  = load_config(cfg_path)
    HERE = os.path.dirname(os.path.abspath(__file__))
    MAP_PATH   = resolve(HERE, cfg["map_output"])
    VOCAB_PATH = resolve(HERE, cfg["vocab_output"])

    print("=" * 65)
    print("FIXING self-map ARTIFACT IN CLUSTER MAP")
    print("=" * 65)

    # Fix cluster map
    cluster_map, total_fixed, domain_counts = fix_cluster_map(MAP_PATH)

    print(f"\n  topic_cluster_map.json")
    print(f"  Total labels fixed: {total_fixed}")
    for domain, count in domain_counts.items():
        if count > 0:
            print(f"    {domain:<35} {count} fixed")

    with open(MAP_PATH, "w", encoding="utf-8") as f:
        json.dump(cluster_map, f, ensure_ascii=False, indent=2)
    print(f"  Written to: {MAP_PATH}")

    # Fix vocabulary
    vocab, removed = fix_vocab(VOCAB_PATH)

    print(f"\n  canonical_topics_v1.json")
    if removed:
        for domain, count in removed.items():
            print(f"    {domain:<35} removed {count} 'self-map' entry")
    else:
        print(f"    No 'self-map' entries found in vocabulary.")

    with open(VOCAB_PATH, "w", encoding="utf-8") as f:
        json.dump(vocab, f, ensure_ascii=False, indent=2)
    print(f"  Written to: {VOCAB_PATH}")

    print(f"\nDone. Run --review-map and apply_topic_map.py as normal.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config_vocab.yaml")
    args = parser.parse_args()
    run(args.config)
