"""subcontinents.py — Which subcontinent each country belongs to (used to relate clues across countries).

Edit the groups below to move a country. Countries not listed anywhere simply don't show up in the picker.
(id, label, ISO alpha-2 codes separated by spaces)
"""

SUBCONTINENTS: list[tuple[str, str, str]] = [
    (
        "south_america",
        "South America",
        "AR BO BR CL CO EC FK GF GY PE PY SR UY VE",
    ),
    (
        "central_america_caribbean",
        "Central America & Caribbean",
        "BZ CR GT HN NI PA SV AG AI AW BB BL BQ BS CU CW DM DO GD GP HT JM KN KY LC MF MQ MS PR SX TC TT VC VG VI",
    ),
    ("north_america", "North America", "CA US MX GL PM BM"),
    (
        "europe",
        "Europe",
        (
            "AD AL AT AX BA BE BG CH CY CZ DE DK EE ES FI FO FR GB GG GI GR HR HU IE IM IS IT JE LI LT LU LV MC "
            "ME MK MT NL NO PL PT RO RS SE SI SJ SK SM VA XK"
        ),
    ),
    ("post_soviet", "Post-Soviet States", "AM AZ BY GE KG KZ MD RU TJ TM UA UZ"),
    ("middle_east", "Middle East", "AE BH IL IQ IR JO KW LB OM PS QA SA SY TR YE"),
    (
        "africa",
        "Africa",
        (
            "DZ AO BJ BW BF BI CM CV CF TD KM CG CD CI DJ EG GQ ER SZ ET GA GM GH GN GW KE LS LR LY MG MW ML MR "
            "MU YT MA MZ NA NE NG RE RW ST SN SC SL SO ZA SS SD TZ TG TN UG EH ZM ZW"
        ),
    ),
    ("east_asia", "East Asia", "CN HK MO TW JP KR KP MN"),
    ("south_asia", "South Asia", "AF BD BT IN LK MV NP PK"),
    ("southeast_asia", "Southeast Asia", "BN ID KH LA MM MY PH SG TH TL VN"),
    (
        "oceania",
        "Oceania",
        "AU NZ PG FJ SB VU WS TO KI FM MH NR PW TV CK NU PF NC GU MP AS CX CC NF PN TK WF",
    ),
]
