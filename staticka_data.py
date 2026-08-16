"""Statická data pro případ, že živý zdroj selže.

Rozpis i tabulka jsou ruční záloha – používají se jen tehdy, když TheSportsDB
ani scraping nic nevrátí. Živá data mají vždycky přednost.
"""

import pandas as pd


def zalohova_tabulka():
    """Statická záloha jen pro úplný výpadek dat."""
    data_tabulka = {
        "Tým": [
            "SK Slavia Praha", "FK Jablonec", "FK Mladá Boleslav", "FK Teplice",
            "FC Hradec Králové", "FC Slovan Liberec", "FC Zbrojovka Brno", "SK Sigma Olomouc",
            "Bohemians Praha 1905", "AC Sparta Praha", "FC Baník Ostrava", "FC Viktoria Plzeň",
            "SK Artis Brno", "1. FC Slovácko", "FK Pardubice", "FC Zlín",
        ],
        "B": [9, 9, 7, 7, 7, 6, 4, 4, 4, 3, 3, 2, 1, 1, 0, 0],
        "Z": [3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3],
        "V": [3, 3, 2, 2, 2, 2, 1, 1, 1, 1, 1, 0, 0, 0, 0, 0],
        "R": [0, 0, 1, 1, 1, 0, 1, 1, 1, 0, 0, 2, 1, 1, 0, 0],
        "P": [0, 0, 0, 0, 0, 1, 1, 1, 1, 2, 2, 1, 2, 2, 3, 3],
        "Skóre": ["11:2", "5:1", "8:4", "9:6", "4:2", "4:2", "4:3", "5:5", "3:3", "4:6", "2:6", "7:9", "4:7", "2:7", "2:6", "1:6"],
    }
    df = pd.DataFrame(data_tabulka)
    df.index = df.index + 1
    return df



# Ruční rozpis sezóny; slouží jen jako záloha při výpadku API.
STATICKA_DATABAZE = {
    1: [
        {"domaci": "SK Slavia Praha", "hoste": "1. FC Slovácko", "datum": "2026-07-20", "stav": "✅ Odehráno", "skore": "3:0", "tip": "1 (Výhra domácích)"},
        {"domaci": "AC Sparta Praha", "hoste": "FK Pardubice", "datum": "2026-07-21", "stav": "✅ Odehráno", "skore": "2:1", "tip": "1 (Výhra domácích)"},
        {"domaci": "FC Viktoria Plzeň", "hoste": "FK Jablonec", "datum": "2026-07-21", "stav": "✅ Odehráno", "skore": "1:1", "tip": "1 (Výhra domácích)"},
        {"domaci": "FC Baník Ostrava", "hoste": "FK Teplice", "datum": "2026-07-21", "stav": "✅ Odehráno", "skore": "0:1", "tip": "1 (Výhra domácích)"},
        {"domaci": "FK Mladá Boleslav", "hoste": "FC Slovan Liberec", "datum": "2026-07-21", "stav": "✅ Odehráno", "skore": "2:1", "tip": "1 (Výhra domácích)"},
        {"domaci": "SK Sigma Olomouc", "hoste": "FC Zlín", "datum": "2026-07-22", "stav": "✅ Odehráno", "skore": "2:0", "tip": "1 (Výhra domácích)"},
        {"domaci": "FC Hradec Králové", "hoste": "Bohemians Praha 1905", "datum": "2026-07-22", "stav": "✅ Odehráno", "skore": "1:0", "tip": "1X (Neprohra domácích)"},
        {"domaci": "FC Zbrojovka Brno", "hoste": "SK Artis Brno", "datum": "2026-07-22", "stav": "✅ Odehráno", "skore": "1:1", "tip": "0 (Remíza)"}
    ],
    2: [
        {"domaci": "FC Baník Ostrava", "hoste": "SK Slavia Praha", "datum": "2026-07-27", "stav": "✅ Odehráno", "skore": "0:2", "tip": "2 (Výhra hostů)"},
        {"domaci": "FK Teplice", "hoste": "AC Sparta Praha", "datum": "2026-07-28", "stav": "✅ Odehráno", "skore": "2:1", "tip": "2 (Výhra hostů)"},
        {"domaci": "FK Jablonec", "hoste": "FK Mladá Boleslav", "datum": "2026-07-28", "stav": "✅ Odehráno", "skore": "2:0", "tip": "1 (Výhra domácích)"},
        {"domaci": "1. FC Slovácko", "hoste": "FC Hradec Králové", "datum": "2026-07-28", "stav": "✅ Odehráno", "skore": "0:1", "tip": "1 (Výhra domácích)"},
        {"domaci": "FC Slovan Liberec", "hoste": "SK Sigma Olomouc", "datum": "2026-07-29", "stav": "✅ Odehráno", "skore": "1:0", "tip": "1 (Výhra domácích)"},
        {"domaci": "Bohemians Praha 1905", "hoste": "FC Viktoria Plzeň", "datum": "2026-07-29", "stav": "✅ Odehráno", "skore": "1:1", "tip": "02 (Neprohra hostů)"},
        {"domaci": "FC Zlín", "hoste": "FC Zbrojovka Brno", "datum": "2026-07-29", "stav": "✅ Odehráno", "skore": "0:2", "tip": "2 (Výhra hostů)"},
        {"domaci": "FK Pardubice", "hoste": "SK Artis Brno", "datum": "2026-07-29", "stav": "✅ Odehráno", "skore": "1:2", "tip": "1 (Výhra domácích)"}
    ],
    3: [
        {"domaci": "SK Slavia Praha", "hoste": "FC Viktoria Plzeň", "datum": "2026-08-03", "stav": "✅ Odehráno", "skore": "2:1", "tip": "1 (Výhra domácích)"},
        {"domaci": "AC Sparta Praha", "hoste": "FC Baník Ostrava", "datum": "2026-08-04", "stav": "✅ Odehráno", "skore": "1:2", "tip": "1 (Výhra domácích)"},
        {"domaci": "FK Mladá Boleslav", "hoste": "FK Teplice", "datum": "2026-08-04", "stav": "✅ Odehráno", "skore": "3:3", "tip": "1 (Výhra domácích)"},
        {"domaci": "FC Hradec Králové", "hoste": "FC Slovan Liberec", "datum": "2026-08-04", "stav": "✅ Odehráno", "skore": "1:0", "tip": "1 (Výhra domácích)"},
        {"domaci": "SK Sigma Olomouc", "hoste": "1. FC Slovácko", "datum": "2026-08-05", "stav": "✅ Odehráno", "skore": "2:1", "tip": "1 (Výhra domácích)"},
        {"domaci": "FK Jablonec", "hoste": "FK Pardubice", "datum": "2026-08-05", "stav": "✅ Odehráno", "skore": "1:0", "tip": "1 (Výhra domácích)"},
        {"domaci": "SK Artis Brno", "hoste": "Bohemians Praha 1905", "datum": "2026-08-05", "stav": "✅ Odehráno", "skore": "1:2", "tip": "2 (Výhra hostů)"},
        {"domaci": "FC Zbrojovka Brno", "hoste": "FC Zlín", "datum": "2026-08-05", "stav": "✅ Odehráno", "skore": "1:0", "tip": "1 (Výhra domácích)"}
    ],
    4: [
        {"domaci": "AC Sparta Praha", "hoste": "FK Teplice", "datum": "2026-08-15 17:00", "stav": "✅ Odehráno", "skore": "4:1", "tip": "1 (Výhra domácích)"},
        {"domaci": "1. FC Slovácko", "hoste": "SK Sigma Olomouc", "datum": "2026-08-15 17:00", "stav": "✅ Odehráno", "skore": "1:2", "tip": "2 (Výhra hostů)"},
        {"domaci": "FK Pardubice", "hoste": "FK Mladá Boleslav", "datum": "2026-08-15 17:00", "stav": "✅ Odehráno", "skore": "1:1", "tip": "0 (Remíza)"},
        {"domaci": "FC Viktoria Plzeň", "hoste": "FC Zlín", "datum": "2026-08-15 20:00", "stav": "✅ Odehráno", "skore": "3:2", "tip": "1 (Výhra domácích)"},
        {"domaci": "FC Baník Ostrava", "hoste": "SK Artis Brno", "datum": "2026-08-16 17:00", "stav": "⏳ Dnes 17:00", "skore": "Zatím nehráno", "tip": "1X (Neprohra domácích)"},
        {"domaci": "FC Slovan Liberec", "hoste": "SK Slavia Praha", "datum": "2026-08-16 20:00", "stav": "⏳ Dnes 20:00", "skore": "Zatím nehráno", "tip": "2 (Výhra hostů)"}
    ],
    5: [
        {"domaci": "FC Zlín", "hoste": "FC Slovan Liberec", "datum": "2026-08-22 17:00", "stav": "🕒 Nadcházející", "skore": "-", "tip": "2 (Výhra hostů)"},
        {"domaci": "SK Artis Brno", "hoste": "AC Sparta Praha", "datum": "2026-08-22 17:00", "stav": "🕒 Nadcházející", "skore": "-", "tip": "2 (Výhra hostů)"},
        {"domaci": "FK Mladá Boleslav", "hoste": "1. FC Slovácko", "datum": "2026-08-22 17:00", "stav": "🕒 Nadcházející", "skore": "-", "tip": "1 (Výhra domácích)"},
        {"domaci": "SK Slavia Praha", "hoste": "Bohemians Praha 1905", "datum": "2026-08-22 20:00", "stav": "🕒 Nadcházející", "skore": "-", "tip": "1 (Výhra domácích)"},
        {"domaci": "FK Jablonec", "hoste": "FC Baník Ostrava", "datum": "2026-08-23 15:00", "stav": "🕒 Nadcházející", "skore": "-", "tip": "0 (Remíza)"},
        {"domaci": "FK Teplice", "hoste": "FC Zbrojovka Brno", "datum": "2026-08-23 17:00", "stav": "🕒 Nadcházející", "skore": "-", "tip": "1X (Neprohra domácích)"},
        {"domaci": "FC Hradec Králové", "hoste": "FC Viktoria Plzeň", "datum": "2026-08-23 17:00", "stav": "🔴 Odloženo", "skore": "-", "tip": "2 (Výhra hostů)"},
        {"domaci": "SK Sigma Olomouc", "hoste": "FK Pardubice", "datum": "2026-08-23 20:00", "stav": "🕒 Nadcházející", "skore": "-", "tip": "1 (Výhra domácích)"}
    ],
    6: [
        {"domaci": "FK Pardubice", "hoste": "SK Artis Brno", "datum": "2026-08-29 17:00", "stav": "🕒 Nadcházející", "skore": "-", "tip": "1X (Neprohra domácích)"},
        {"domaci": "Bohemians Praha 1905", "hoste": "FK Mladá Boleslav", "datum": "2026-08-29 17:00", "stav": "🕒 Nadcházející", "skore": "-", "tip": "0 (Remíza)"},
        {"domaci": "FC Zbrojovka Brno", "hoste": "FC Zlín", "datum": "2026-08-29 17:00", "stav": "🕒 Nadcházející", "skore": "-", "tip": "1 (Výhra domácích)"},
        {"domaci": "FC Baník Ostrava", "hoste": "SK Sigma Olomouc", "datum": "2026-08-29 20:00", "stav": "🕒 Nadcházející", "skore": "-", "tip": "1 (Výhra domácích)"},
        {"domaci": "FC Slovan Liberec", "hoste": "FC Hradec Králové", "datum": "2026-08-30 15:00", "stav": "🕒 Nadcházející", "skore": "-", "tip": "1X (Neprohra domácích)"},
        {"domaci": "FC Viktoria Plzeň", "hoste": "1. FC Slovácko", "datum": "2026-08-30 17:00", "stav": "🕒 Nadcházející", "skore": "-", "tip": "1 (Výhra domácích)"},
        {"domaci": "FK Teplice", "hoste": "FK Jablonec", "datum": "2026-08-30 17:00", "stav": "🕒 Nadcházející", "skore": "-", "tip": "02 (Neprohra hostů)"},
        {"domaci": "AC Sparta Praha", "hoste": "SK Slavia Praha", "datum": "2026-08-30 20:00", "stav": "🕒 Nadcházející", "skore": "-", "tip": "0 (Remíza)"}
    ],
    7: [
        {"domaci": "1. FC Slovácko", "hoste": "FK Pardubice", "datum": "2026-09-05 17:00", "stav": "🕒 Nadcházející", "skore": "-", "tip": "1 (Výhra domácích)"},
        {"domaci": "SK Sigma Olomouc", "hoste": "Bohemians Praha 1905", "datum": "2026-09-05 17:00", "stav": "🕒 Nadcházející", "skore": "-", "tip": "1X (Neprohra domácích)"},
        {"domaci": "FK Mladá Boleslav", "hoste": "FC Baník Ostrava", "datum": "2026-09-05 17:00", "stav": "🕒 Nadcházející", "skore": "-", "tip": "1 (Výhra domácích)"},
        {"domaci": "SK Artis Brno", "hoste": "FC Viktoria Plzeň", "datum": "2026-09-05 17:00", "stav": "🕒 Nadcházející", "skore": "-", "tip": "2 (Výhra hostů)"},
        {"domaci": "FK Jablonec", "hoste": "FC Slovan Liberec", "datum": "2026-09-06 15:00", "stav": "🕒 Nadcházející", "skore": "-", "tip": "0 (Remíza)"},
        {"domaci": "FC Hradec Králové", "hoste": "AC Sparta Praha", "datum": "2026-09-06 17:00", "stav": "🕒 Nadcházející", "skore": "-", "tip": "2 (Výhra hostů)"},
        {"domaci": "FC Zlín", "hoste": "FK Teplice", "datum": "2026-09-06 17:00", "stav": "🕒 Nadcházející", "skore": "-", "tip": "1X (Neprohra domácích)"},
        {"domaci": "SK Slavia Praha", "hoste": "FC Zbrojovka Brno", "datum": "2026-09-06 20:00", "stav": "🕒 Nadcházející", "skore": "-", "tip": "1 (Výhra domácích)"}
    ]
}

