import json
import requests
import csv
from tqdm import tqdm
from pathlib import Path
import os
import itertools

BASE_URL = "https://api.dofusdb.fr"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

INPUT_FILE = os.path.join(BASE_DIR, 'monsters_originel.json')
OUTPUT_FILE = os.path.join(BASE_DIR, 'monsters.json')
ERROR_FILE = os.path.join(BASE_DIR, 'erreurs.csv')

TIMEOUT = 15


session = requests.Session()

race_cache = {}
subarea_cache = {}


def log_error(writer, monster_id, name, error, details=""):
    writer.writerow({
        "id": monster_id,
        "name": name,
        "error": error,
        "details": details
    })


def get_json(url, params=None):
    r = session.get(url, params=params, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()

def generate_name_variants(name):
    chars = list(name)

    candidate_positions = []

    for i, c in enumerate(chars):
        # Après apostrophe
        if i > 0 and chars[i - 1] == "'" and c.isalpha():
            candidate_positions.append(i)

        # Après espace ET pas suivi d'apostrophe
        if i > 0 and chars[i - 1] == " " and c.isalpha():
            if i + 1 < len(chars) and chars[i + 1] == "'":
                continue
            candidate_positions.append(i)

    candidate_positions = sorted(set(candidate_positions))

    variants = set()

    # Génère toutes les combinaisons de majuscules possibles
    for r in range(1, len(candidate_positions) + 1):
        for combo in itertools.combinations(candidate_positions, r):
            new_chars = chars.copy()
            for idx in combo:
                new_chars[idx] = new_chars[idx].upper()
            variants.add("".join(new_chars))

    return list(variants)


def get_monster_by_name(name):
    # Tentative directe
    data = get_json(f"{BASE_URL}/monsters", params={"name.fr": name})
    items = data.get("data", [])

    if items:
        return items

    # Fallback : variantes de majuscules
    variants = generate_name_variants(name)

    for v in variants:
        try:
            data = get_json(f"{BASE_URL}/monsters", params={"name.fr": v})
            items = data.get("data", [])
            if items:
                return items
        except Exception:
            continue

    return []


def get_race_name(race_id):
    if race_id in race_cache:
        return race_cache[race_id]

    data = get_json(f"{BASE_URL}/monster-races/{race_id}")
    name = data.get("name", {}).get("fr")

    race_cache[race_id] = name
    return name


def get_subarea_name(subarea_id):
    if subarea_id in subarea_cache:
        return subarea_cache[subarea_id]

    data = get_json(f"{BASE_URL}/subareas/{subarea_id}")
    name = data.get("name", {}).get("fr")

    subarea_cache[subarea_id] = name
    return name

def assign_dungeons_reverse(enriched, writer):
    """
    Parcourt TOUS les donjons un par un (/dungeons/{id})
    et assigne le donjon aux monstres via id_DB
    """

    # Mapping rapide id_DB -> monstre
    monster_map = {}
    for m in enriched:
        if m.get("id_DB") is not None:
            monster_map[m["id_DB"]] = m

    # Récupération liste des ids de donjons
    data = get_json(f"{BASE_URL}/dungeons", params={"limit": 500})
    dungeon_list = data.get("data", [])

    print(f"{len(dungeon_list)} donjons détectés")

    """for d in tqdm(dungeon_list, desc="Scan donjons"):"""
    for dungeon_id in range(1, 188):
        """d = get_json(f"{BASE_URL}/dungeons/" + str(dun_id))
        dungeon_id = d.get("id")"""
        if dungeon_id is None:
            continue

        try:
            dungeon = get_json(f"{BASE_URL}/dungeons/{dungeon_id}")

            dungeon_name = dungeon.get("name", {}).get("fr")
            monsters = dungeon.get("monsters", [])

            if not dungeon_name or not monsters:
                continue

            for mid in monsters:
                if mid in monster_map:
                    m = monster_map[mid]

                    # Initialise la liste si nécessaire
                    if "donjon" not in m or m["donjon"] is None:
                        m["donjon"] = []

                    # Ajoute le donjon s'il n'est pas déjà présent
                    if dungeon_name not in m["donjon"]:
                        m["donjon"].append(dungeon_name)

                    # Retire le donjon des zones si présent
                    if dungeon_name in m.get("zones", []):
                        m["zones"] = [z for z in m["zones"] if z != dungeon_name]

                    # Retire le donjon des zones si présent
                    if dungeon_name in m.get("zones", []):
                        m["zones"] = [z for z in m["zones"] if z != dungeon_name]

        except Exception as e:
            print(f"Erreur donjon {dungeon_id}: {e}")

def main():
    with open(INPUT_FILE, "r", encoding="utf-8") as f:
        original = json.load(f)

    enriched = []

    with open(ERROR_FILE, "w", newline="", encoding="utf-8") as csvfile:
        fieldnames = ["id", "name", "error", "details"]
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()

        # Première passe : monstres
        for m in tqdm(original, desc="Monstres"):
            oid = m.get("id")
            name = m.get("name")

            base_entry = {
                "id": oid,
                "name": name,
                "step": m.get("step"),
                "type": m.get("type"),
                "id_DB": None,
                "famille": None,
                "zones": [],
                "donjon": None
            }

            try:
                results = get_monster_by_name(name)

                if not results:
                    log_error(writer, oid, name, "NotFound", "Aucun résultat API")
                    enriched.append(base_entry)
                    continue

                monster = results[0]

                # Normalise le nom depuis l'API
                api_name = monster.get("name", {}).get("fr")
                if api_name:
                    base_entry["name"] = api_name

                monster_db_id = monster.get("id")
                base_entry["id_DB"] = monster_db_id

                monster_db_id = monster.get("id")
                base_entry["id_DB"] = monster_db_id

                # Famille
                race_id = monster.get("race")
                if race_id is not None:
                    try:
                        famille = get_race_name(race_id)
                        base_entry["famille"] = famille
                    except Exception as e:
                        log_error(writer, oid, name, "RaceError", str(e))

                # Zones (subareas)
                zones = []
                for sub_id in monster.get("subareas", []):
                    try:
                        zname = get_subarea_name(sub_id)
                        if zname:
                            zones.append(zname)
                    except Exception as e:
                        log_error(writer, oid, name, "SubareaError", f"{sub_id}: {e}")

                base_entry["zones"] = sorted(list(set(zones)))

            except Exception as e:
                log_error(writer, oid, name, "MonsterAPIError", str(e))

            enriched.append(base_entry)

        # Deuxième passe : donjons
        assign_dungeons_reverse(enriched, writer)

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(enriched, f, ensure_ascii=False, indent=2)

    print("\nTerminé.")
    print(f"- JSON : {OUTPUT_FILE}")
    print(f"- CSV erreurs : {ERROR_FILE}")


if __name__ == "__main__":
    main()
