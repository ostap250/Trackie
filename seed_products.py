"""
seed_products.py — Populate the products table with common foods.
Run once before starting the bot: python seed_products.py
Safe to re-run — skips seeding if global products already exist.
"""

from database import Database

PRODUCTS = [
    # (name, calories_per_100g, protein_per_100g)

    # ── Protein sources ────────────────────────────────────────────────────
    ("Chicken breast (cooked)",     165,  31.0),
    ("Chicken thigh (cooked)",      209,  26.0),
    ("Beef (lean, cooked)",         250,  26.0),
    ("Pork loin (cooked)",          242,  27.0),
    ("Salmon (cooked)",             208,  20.0),
    ("Tuna (canned in water)",      116,  26.0),
    ("Egg (whole)",                 155,  13.0),
    ("Egg white",                    52,  11.0),
    ("Cottage cheese",               98,  11.0),
    ("Greek yogurt (0%)",            59,  10.0),
    ("Whey protein powder",         400,  80.0),
    ("Shrimp (cooked)",              99,  24.0),
    ("Turkey breast (cooked)",      189,  29.0),

    # ── Dairy ──────────────────────────────────────────────────────────────
    ("Milk (whole, 3.2%)",           61,   3.2),
    ("Milk (skim, 0.5%)",            35,   3.4),
    ("Hard cheese (e.g. Gouda)",    356,  25.0),
    ("Mozzarella",                  280,  28.0),
    ("Butter",                      717,   0.9),

    # ── Grains & carbs (dry weight) ────────────────────────────────────────
    ("Oatmeal (dry)",               389,  17.0),
    ("Rice white (dry)",            365,   7.0),
    ("Rice brown (dry)",            370,   8.0),
    ("Buckwheat (dry)",             343,  13.0),
    ("Pasta (dry)",                 371,  13.0),
    ("Bread (white)",               265,   9.0),
    ("Bread (whole wheat)",         247,  13.0),
    ("Potato",                       77,   2.0),
    ("Sweet potato",                 86,   1.6),
    ("Quinoa (dry)",                368,  14.0),

    # ── Fruits ─────────────────────────────────────────────────────────────
    ("Banana",                       89,   1.1),
    ("Apple",                        52,   0.3),
    ("Orange",                       47,   0.9),
    ("Blueberries",                  57,   0.7),
    ("Strawberries",                 32,   0.7),

    # ── Vegetables ─────────────────────────────────────────────────────────
    ("Broccoli",                     34,   2.8),
    ("Spinach",                      23,   2.9),
    ("Tomato",                       18,   0.9),
    ("Cucumber",                     15,   0.7),
    ("Bell pepper",                  31,   1.0),
    ("Carrot",                       41,   0.9),

    # ── Fats & nuts ────────────────────────────────────────────────────────
    ("Olive oil",                   884,   0.0),
    ("Almonds",                     579,  21.0),
    ("Walnuts",                     654,  15.0),
    ("Peanut butter",               588,  25.0),
    ("Avocado",                     160,   2.0),

    # ── Other ──────────────────────────────────────────────────────────────
    ("Creatine powder",               0,   0.0),
    ("Protein bar (avg)",           370,  30.0),
    ("Dark chocolate (70%+)",       598,   8.0),
    ("Honey",                       304,   0.3),
]


if __name__ == "__main__":
    db = Database("trackie.db")
    if db.is_products_seeded():
        print("Products already seeded — skipping.")
    else:
        db.seed_global_products(PRODUCTS)
        print(f"Seeded {len(PRODUCTS)} products into the database.")
