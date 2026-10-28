"""Reference data: specialties, procedure and diagnosis codes, metros and payers.

Prices are typical allowed amounts in USD, rounded; minutes are typical face time,
used to detect "impossible days" (more billed time than a person can work).
"""

# specialty -> (kind, share of providers)
SPECIALTIES = {
    "Family Medicine": ("primary", 17),
    "Internal Medicine": ("primary", 13),
    "Pediatrics": ("primary", 6),
    "Cardiology": ("specialist", 6),
    "Orthopedics": ("specialist", 5),
    "Neurology": ("specialist", 4),
    "Dermatology": ("specialist", 4),
    "Oncology": ("specialist", 3),
    "Psychiatry": ("specialist", 5),
    "Pulmonology": ("specialist", 3),
    "Gastroenterology": ("specialist", 3),
    "Endocrinology": ("specialist", 3),
    "Radiology": ("ancillary", 6),
    "Clinical Laboratory": ("ancillary", 7),
    "Physical Therapy": ("ancillary", 7),
    "DME Supplier": ("ancillary", 3),
}
PRIMARY = [s for s, (k, _) in SPECIALTIES.items() if k == "primary"]
SPECIALISTS = [s for s, (k, _) in SPECIALTIES.items() if k == "specialist"]
ANCILLARY = [s for s, (k, _) in SPECIALTIES.items() if k == "ancillary"]

# code -> (description, price, minutes)
EM_CODES = {
    "99211": ("Office visit, established, minimal", 25.0, 5),
    "99212": ("Office visit, established, straightforward", 58.0, 12),
    "99213": ("Office visit, established, low complexity", 92.0, 20),
    "99214": ("Office visit, established, moderate complexity", 131.0, 30),
    "99215": ("Office visit, established, high complexity", 184.0, 40),
}
EM_ORDER = list(EM_CODES)
# Typical E/M level mix per kind of practice (weights over 99211..99215).
EM_MIX = {
    "primary": [4, 18, 46, 26, 6],
    "specialist": [2, 10, 38, 36, 14],
    "Oncology": [1, 4, 22, 41, 32],  # complex patients: legitimately high top-level share
    "Psychiatry": [2, 12, 40, 34, 12],
}
EM_UPCODED = [0, 2, 8, 30, 60]

PROCEDURES = {
    "Family Medicine": [("90471", "Immunization administration", 25.0, 5), ("36415", "Venipuncture", 8.0, 5),
                        ("93000", "Electrocardiogram, complete", 30.0, 10)],
    "Internal Medicine": [("36415", "Venipuncture", 8.0, 5), ("93000", "Electrocardiogram, complete", 30.0, 10),
                          ("94010", "Spirometry", 40.0, 15)],
    "Pediatrics": [("90471", "Immunization administration", 25.0, 5), ("96110", "Developmental screening", 12.0, 10)],
    "Cardiology": [("93000", "Electrocardiogram, complete", 30.0, 10), ("93306", "Echocardiogram, complete", 250.0, 45),
                   ("93015", "Cardiovascular stress test", 180.0, 40), ("93224", "Holter monitor, 48h", 95.0, 15)],
    "Orthopedics": [("20610", "Arthrocentesis, major joint", 110.0, 15), ("29881", "Knee arthroscopy", 1150.0, 75),
                    ("20550", "Tendon sheath injection", 70.0, 10)],
    "Neurology": [("95816", "EEG, awake and drowsy", 300.0, 60), ("95910", "Nerve conduction studies", 220.0, 45),
                  ("64615", "Chemodenervation for migraine", 210.0, 20)],
    "Dermatology": [("11102", "Tangential skin biopsy", 110.0, 15), ("17000", "Destruction of lesion", 75.0, 10),
                    ("11042", "Debridement, subcutaneous", 130.0, 20)],
    "Oncology": [("96413", "Chemotherapy infusion, first hour", 280.0, 60),
                 ("96365", "IV infusion, first hour", 160.0, 60), ("96375", "IV push, additional", 45.0, 10)],
    "Psychiatry": [("90834", "Psychotherapy, 45 minutes", 120.0, 45), ("90837", "Psychotherapy, 60 minutes", 160.0, 60),
                   ("90792", "Psychiatric evaluation with medical services", 220.0, 60)],
    "Pulmonology": [("94010", "Spirometry", 40.0, 15), ("94060", "Bronchodilation responsiveness", 65.0, 25),
                    ("94618", "Pulmonary stress testing", 70.0, 30)],
    "Gastroenterology": [("45378", "Colonoscopy, diagnostic", 690.0, 45), ("43239", "Upper GI endoscopy with biopsy", 540.0, 30)],
    "Endocrinology": [("95251", "Continuous glucose monitoring analysis", 35.0, 15), ("83036", "Hemoglobin A1c", 13.0, 5)],
    "Radiology": [("71046", "Chest X-ray, 2 views", 45.0, 10), ("74177", "CT abdomen and pelvis with contrast", 480.0, 20),
                  ("70553", "MRI brain with and without contrast", 640.0, 45), ("73721", "MRI lower extremity joint", 430.0, 40)],
    "Physical Therapy": [("97110", "Therapeutic exercise, 15 min", 34.0, 15), ("97140", "Manual therapy, 15 min", 32.0, 15),
                         ("97530", "Therapeutic activities, 15 min", 38.0, 15), ("97161", "PT evaluation, low complexity", 105.0, 30)],
    "DME Supplier": [("E0601", "CPAP device", 95.0, 0), ("K0001", "Standard wheelchair", 85.0, 0),
                     ("E0114", "Crutches, underarm", 40.0, 0), ("L1833", "Knee orthosis", 120.0, 0)],
}
LAB_PANEL = ("80053", "Comprehensive metabolic panel", 15.0, 0)
LAB_COMPONENTS = [("82947", "Glucose, quantitative", 6.0, 0), ("84132", "Potassium, serum", 6.0, 0),
                  ("82565", "Creatinine, blood", 7.0, 0), ("84295", "Sodium, serum", 6.0, 0),
                  ("82310", "Calcium, total", 7.0, 0), ("84520", "Urea nitrogen (BUN)", 6.0, 0),
                  ("82040", "Albumin, serum", 7.0, 0)]
LAB_OTHER = [("85025", "Complete blood count with differential", 11.0, 0), ("83036", "Hemoglobin A1c", 13.0, 0),
             ("80061", "Lipid panel", 18.0, 0), ("84443", "Thyroid stimulating hormone", 23.0, 0)]
PROCEDURES["Clinical Laboratory"] = [LAB_PANEL, *LAB_OTHER]

CODE_INFO = {code: (desc, price, minutes) for code, (desc, price, minutes) in EM_CODES.items()}
for _rows in PROCEDURES.values():
    for _code, _desc, _price, _minutes in _rows:
        CODE_INFO[_code] = (_desc, _price, _minutes)
for _code, _desc, _price, _minutes in LAB_COMPONENTS:
    CODE_INFO[_code] = (_desc, _price, _minutes)

DIAGNOSES = {
    "primary": ["Z00.00", "I10", "E11.9", "E78.5", "J06.9", "M54.50", "R05.9", "Z23", "F41.1", "K21.9"],
    "Cardiology": ["I10", "I25.10", "I48.91", "I50.9", "R07.9"],
    "Orthopedics": ["M17.11", "M25.561", "S83.241A", "M75.101"],
    "Neurology": ["G43.909", "G40.909", "G62.9", "R51.9"],
    "Dermatology": ["L40.0", "L57.0", "D48.5", "L70.0"],
    "Oncology": ["C50.911", "C34.90", "C18.9", "D63.0"],
    "Psychiatry": ["F32.9", "F41.1", "F43.10", "F90.0"],
    "Pulmonology": ["J45.909", "J44.9", "G47.33", "R06.02"],
    "Gastroenterology": ["K21.9", "K57.30", "Z12.11", "K50.90"],
    "Endocrinology": ["E11.65", "E03.9", "E05.90", "E21.3"],
    "Radiology": ["R10.9", "R91.8", "M25.561", "R51.9"],
    "Clinical Laboratory": ["E11.9", "E78.5", "Z00.00", "N18.3"],
    "Physical Therapy": ["M54.50", "M25.561", "S83.241A", "M62.81"],
    "DME Supplier": ["G47.33", "M17.11", "R26.89", "Z99.89"],
}

# name -> (state, lat, lon, zip prefix)
METROS = {
    "New York": ("NY", 40.7128, -74.0060, "100"),
    "Los Angeles": ("CA", 34.0522, -118.2437, "900"),
    "Chicago": ("IL", 41.8781, -87.6298, "606"),
    "Houston": ("TX", 29.7604, -95.3698, "770"),
    "Phoenix": ("AZ", 33.4484, -112.0740, "850"),
    "Philadelphia": ("PA", 39.9526, -75.1652, "191"),
    "Miami": ("FL", 25.7617, -80.1918, "331"),
    "Atlanta": ("GA", 33.7490, -84.3880, "303"),
}

# Fictional payers. Each sends claims in its own shape; one sends X12 837P.
PAYERS = {
    "PYR-01": ("BlueRiver Health", "json-a", 0.78),
    "PYR-02": ("Meridian Medicare Advantage", "837P", 0.70),
    "PYR-03": ("Summit Mutual", "json-b", 0.82),
    "PYR-04": ("Keystone Care", "json-a", 0.75),
}

FIRST = ["James", "Mary", "Robert", "Patricia", "John", "Jennifer", "Michael", "Linda", "David", "Elizabeth",
         "William", "Barbara", "Richard", "Susan", "Joseph", "Jessica", "Thomas", "Sarah", "Priya", "Arjun",
         "Wei", "Mei", "Carlos", "Sofia", "Ahmed", "Fatima", "Kenji", "Aiko", "Olu", "Amara", "Ivan", "Elena",
         "Raj", "Ananya", "Luca", "Chiara", "Noah", "Emma", "Liam", "Olivia", "Mateo", "Isabella", "Kwame",
         "Zara", "Diego", "Hana", "Omar", "Leila", "Sven", "Ingrid"]
LAST = ["Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller", "Davis", "Rodriguez", "Martinez",
        "Hernandez", "Lopez", "Gonzalez", "Wilson", "Anderson", "Thomas", "Taylor", "Moore", "Jackson", "Martin",
        "Lee", "Patel", "Sharma", "Chen", "Wang", "Kim", "Nguyen", "Okafor", "Haddad", "Rossi", "Novak",
        "Tanaka", "Iyer", "Reddy", "Khan", "Silva", "Cohen", "Murphy", "Schmidt", "Dubois", "Kowalski",
        "Mensah", "Ali", "Park", "Costa", "Bauer", "Larsen", "Ortiz", "Rahman", "Fischer"]
STREETS = ["Oak", "Maple", "Cedar", "Pine", "Elm", "Lake", "Hill", "Park", "Washington", "Lincoln", "Sunset",
           "Riverside", "Highland", "Madison", "Jefferson", "Franklin", "Harbor", "Willow", "Spring", "Union"]
ORG_SUFFIX = {"Radiology": "Imaging Center", "Clinical Laboratory": "Diagnostics Lab",
              "Physical Therapy": "Physical Therapy", "DME Supplier": "Medical Supply"}
