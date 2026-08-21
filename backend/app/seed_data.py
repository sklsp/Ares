"""Demo catalog.

42 products across six categories. The spread is deliberate: some products
have strong copy, some are thin, some are empty or placeholder, stock ranges
from zero to overstocked, and sales volume varies by an order of magnitude.
That variety is what gives the agent something real to reason about.

Fields: sku, title, description, price, category, stock, reorder_point, demand
`demand` (0-5) drives how many orders get generated for the product.
"""

from __future__ import annotations

from typing import NamedTuple


class SeedProduct(NamedTuple):
    sku: str
    title: str
    description: str
    price: float
    category: str
    stock: int
    reorder_point: int
    demand: int


RUNNING = "Running Shoes"
GYM = "Gym Equipment"
SUPPLEMENTS = "Supplements"
CLOTHING = "Clothing"
ACCESSORIES = "Accessories"
ELECTRONICS = "Electronics"

PRODUCTS: list[SeedProduct] = [
    # ---------------- Running Shoes ----------------
    SeedProduct(
        "RUN-001",
        "Velocity Pro 5 Road Running Shoe",
        "The Velocity Pro 5 is a daily trainer for runners logging 40 to 80 km a week. "
        "A 32 mm nitrogen-infused foam midsole returns energy on long efforts while the "
        "8 mm drop keeps your stride familiar. The engineered mesh upper weighs 249 g in "
        "a men's size 9 and dries quickly after wet runs. Ideal for tempo sessions and "
        "weekend long runs on tarmac. Machine washable at 30 degrees, air dry only.",
        139.00,
        RUNNING,
        84,
        20,
        5,
    ),
    SeedProduct(
        "RUN-002",
        "Trail Grip 3 All-Terrain Runner",
        "Built for loose gravel, wet roots and steep descents. A 4 mm lugged rubber "
        "outsole bites into soft ground and the rock plate under the forefoot blocks "
        "sharp stones. The ripstop upper resists tearing on scrub. Weighs 295 g. "
        "Ideal for trail runners and fast hikers covering mixed terrain.",
        149.00,
        RUNNING,
        41,
        15,
        4,
    ),
    SeedProduct(
        "RUN-003",
        "Running Shoes X",
        "Comfortable running shoes.",
        89.00,
        RUNNING,
        112,
        20,
        3,
    ),
    SeedProduct(
        "RUN-004",
        "Cloudstep Recovery Trainer",
        "A soft, wide-platform shoe for the day after a hard session. 36 mm of "
        "compression-moulded foam absorbs impact and the roomy toe box lets swollen "
        "feet spread out. Weighs 268 g. Ideal for easy recovery miles and long days "
        "on your feet. Removable insole for custom orthotics.",
        119.00,
        RUNNING,
        7,
        20,
        4,
    ),
    SeedProduct(
        "RUN-005",
        "SPEEDSTER RACE FLAT",
        "Fast shoe for racing. Very light.",
        169.00,
        RUNNING,
        23,
        10,
        2,
    ),
    SeedProduct(
        "RUN-006",
        "Meridian Stability 2 Support Shoe",
        "Guides overpronating runners without feeling rigid. A firmer foam wedge on the "
        "medial side supports the arch through mid-stance, and the 10 mm drop suits "
        "heel strikers. 302 g in a men's size 9, available in standard and wide. "
        "Ideal for higher-mileage runners who need structure under fatigue.",
        129.00,
        RUNNING,
        56,
        20,
        3,
    ),
    SeedProduct(
        "RUN-007",
        "Kids Sprint Runner",
        "",
        54.00,
        RUNNING,
        68,
        15,
        2,
    ),
    # ---------------- Gym Equipment ----------------
    SeedProduct(
        "GYM-001",
        "Cast Iron Adjustable Dumbbell Set 2-24 kg",
        "One pair replaces fifteen. Each handle adjusts from 2 kg to 24 kg in 2 kg "
        "increments with a twist-lock collar that seats the plates before you lift. "
        "Cast iron plates with a powder-coat finish, knurled steel handles, and a "
        "moulded cradle that keeps the set tidy. Ideal for home gyms where floor "
        "space is limited. Wipe down with a dry cloth after use.",
        349.00,
        GYM,
        18,
        8,
        5,
    ),
    SeedProduct(
        "GYM-002",
        "Olympic Barbell 20 kg",
        "A 2200 mm bar rated to 680 kg with dual knurl marks for powerlifting and "
        "weightlifting. Bronze bushings keep the sleeves spinning through cleans and "
        "snatches. 190,000 PSI tensile steel with a black zinc shaft and chrome "
        "sleeves. Ideal for a garage gym platform or a commercial floor.",
        279.00,
        GYM,
        12,
        6,
        3,
    ),
    SeedProduct(
        "GYM-003",
        "Yoga Mat",
        "Good product for yoga. High quality product.",
        29.00,
        GYM,
        204,
        30,
        4,
    ),
    SeedProduct(
        "GYM-004",
        "Competition Kettlebell 16 kg",
        "Single-cast steel with a 33 mm handle and a 280 mm base, matching competition "
        "dimensions so the size stays the same as you move up in weight. The flat "
        "bottom lets you use it for renegade rows without rocking. Ideal for swings, "
        "get-ups and long cycle sets.",
        89.00,
        GYM,
        3,
        10,
        4,
    ),
    SeedProduct(
        "GYM-005",
        "Resistance Band Set",
        "Bands for training.",
        34.00,
        GYM,
        156,
        25,
        3,
    ),
    SeedProduct(
        "GYM-006",
        "Foldable Weight Bench with Incline",
        "Adjusts through seven back positions from -20 to 85 degrees and folds flat to "
        "230 mm for storage under a bed. The 50 mm steel frame is rated to 300 kg "
        "including the lifter, and the 40 mm high-density pad resists bottoming out "
        "under heavy presses. Ideal for a home gym that has to disappear between sessions.",
        199.00,
        GYM,
        27,
        10,
        3,
    ),
    SeedProduct(
        "GYM-007",
        "Foam Roller 60cm",
        "Roller for muscles. Must have.",
        24.00,
        GYM,
        88,
        20,
        2,
    ),
    # ---------------- Supplements ----------------
    SeedProduct(
        "SUP-001",
        "Whey Protein Isolate 1 kg Vanilla",
        "27 g of protein and 0.5 g of sugar per 30 g scoop, cold-filtered so it mixes "
        "clear rather than chalky. Third-party tested for banned substances, batch "
        "numbers printed on every tub. 33 servings. Ideal for the 30 minutes after "
        "training or as a between-meals top-up. Store sealed in a dry cupboard.",
        54.00,
        SUPPLEMENTS,
        142,
        40,
        5,
    ),
    SeedProduct(
        "SUP-002",
        "Creatine Monohydrate 500 g",
        "Micronised creatine monohydrate, 5 g per serving, 100 servings per pouch. "
        "Unflavoured and fine enough to dissolve in water without grit. No loading "
        "phase needed at 5 g daily. Ideal for anyone lifting or sprinting who wants "
        "the most researched supplement on the shelf.",
        29.00,
        SUPPLEMENTS,
        96,
        30,
        5,
    ),
    SeedProduct(
        "SUP-003",
        "Pre Workout",
        "Energy powder.",
        39.00,
        SUPPLEMENTS,
        61,
        25,
        3,
    ),
    SeedProduct(
        "SUP-004",
        "Electrolyte Hydration Tablets - Citrus",
        "Twelve effervescent tablets per tube, each delivering 320 mg of sodium and "
        "80 mg of magnesium with only 4 calories. Drop one in 500 ml of water. Ideal "
        "for long summer sessions, hot yoga or the day after a race when plain water "
        "is not cutting it.",
        12.00,
        SUPPLEMENTS,
        0,
        30,
        4,
    ),
    SeedProduct(
        "SUP-005",
        "Omega 3 Fish Oil 120 Capsules",
        "Each capsule provides 660 mg of EPA and 440 mg of DHA from wild-caught "
        "sardines and anchovies, with a lemon coating that prevents fish burps. "
        "Two-month supply at two capsules a day. Ideal for anyone who does not eat "
        "oily fish twice a week.",
        26.00,
        SUPPLEMENTS,
        73,
        25,
        2,
    ),
    SeedProduct(
        "SUP-006",
        "Magnesium Glycinate",
        "TODO write description",
        19.00,
        SUPPLEMENTS,
        4,
        25,
        3,
    ),
    SeedProduct(
        "SUP-007",
        "Plant Protein Blend 900 g Chocolate",
        "Pea and brown rice protein blended to a complete amino acid profile, 24 g of "
        "protein per serving with 4 g of fibre. Sweetened with cocoa and a little "
        "stevia, no gums. 30 servings. Ideal for vegan athletes or anyone who does "
        "not tolerate dairy.",
        49.00,
        SUPPLEMENTS,
        38,
        20,
        3,
    ),
    # ---------------- Clothing ----------------
    SeedProduct(
        "CLO-001",
        "Merino Base Layer Long Sleeve",
        "17.5 micron merino wool at 190 gsm, soft enough to wear against bare skin all "
        "day. Wool regulates temperature and resists odour, so it handles a cold start "
        "and a warm finish without a change of top. Flatlock seams sit away from pack "
        "straps. Ideal for winter running and layering under a shell. Machine wash cold.",
        89.00,
        CLOTHING,
        45,
        15,
        4,
    ),
    SeedProduct(
        "CLO-002",
        "Training Shorts",
        "Shorts for training. Nice product.",
        34.00,
        CLOTHING,
        178,
        30,
        4,
    ),
    SeedProduct(
        "CLO-003",
        "Lightweight Running Jacket",
        "A 108 g windproof shell that packs into its own chest pocket. The front panel "
        "blocks wind chill while the laser-perforated back lets heat escape on climbs. "
        "Reflective trim on both sleeves. Ideal for shoulder-season mornings when the "
        "temperature swings ten degrees between the start and the finish.",
        119.00,
        CLOTHING,
        9,
        15,
        3,
    ),
    SeedProduct(
        "CLO-004",
        "Compression Leggings",
        "",
        59.00,
        CLOTHING,
        64,
        20,
        3,
    ),
    SeedProduct(
        "CLO-005",
        "Everyday Cotton Training Tee",
        "220 gsm combed cotton with a touch of elastane so it keeps its shape through "
        "repeated washes. Pre-shrunk, side-seamed, and cut straight rather than "
        "slim. Ideal for gym sessions and the walk home afterwards. Available from "
        "XS to 3XL.",
        29.00,
        CLOTHING,
        231,
        40,
        4,
    ),
    SeedProduct(
        "CLO-006",
        "SPORTS BRA HIGH SUPPORT",
        "Bra for running.",
        49.00,
        CLOTHING,
        52,
        20,
        3,
    ),
    SeedProduct(
        "CLO-007",
        "Thermal Running Tights",
        "Brushed inner face traps warmth down to about -5 degrees while the four-way "
        "stretch fabric moves with your stride. A zipped back pocket holds a phone "
        "without bouncing and the ankle zips clear winter shoes. Ideal for dark "
        "January mornings.",
        79.00,
        CLOTHING,
        31,
        15,
        2,
    ),
    # ---------------- Accessories ----------------
    SeedProduct(
        "ACC-001",
        "Insulated Stainless Steel Bottle 750 ml",
        "Double-walled 18/8 stainless steel keeps drinks cold for 24 hours and hot for "
        "12. The 53 mm mouth takes ice cubes and a bottle brush, and the lid seals "
        "against leaks in a gym bag. 420 g empty. Ideal for the gym, the office and "
        "long drives. Hand wash the lid.",
        32.00,
        ACCESSORIES,
        124,
        30,
        4,
    ),
    SeedProduct(
        "ACC-002",
        "Gym Bag",
        "Bag for the gym.",
        45.00,
        ACCESSORIES,
        87,
        25,
        3,
    ),
    SeedProduct(
        "ACC-003",
        "Lifting Straps - Cotton",
        "1.5 mm heavy cotton webbing, 550 mm long, with a neoprene wrist pad that stops "
        "the strap digging in on heavy pulls. Sold as a pair. Ideal for deadlifts, "
        "rows and shrugs once grip fails before the target muscle does.",
        18.00,
        ACCESSORIES,
        6,
        20,
        4,
    ),
    SeedProduct(
        "ACC-004",
        "Running Belt",
        "Belt that holds your phone.",
        24.00,
        ACCESSORIES,
        143,
        30,
        2,
    ),
    SeedProduct(
        "ACC-005",
        "Cushioned Running Socks - 3 Pack",
        "Targeted cushioning under the heel and forefoot with a thin mesh panel over "
        "the arch for airflow. The 62 percent merino blend manages sweat without "
        "going damp. Three pairs per pack, sizes 36 to 47. Ideal for long runs where "
        "hot spots turn into blisters.",
        28.00,
        ACCESSORIES,
        198,
        40,
        4,
    ),
    SeedProduct(
        "ACC-006",
        "Massage Gun",
        "Best product for muscles. You will love it.",
        149.00,
        ACCESSORIES,
        19,
        10,
        3,
    ),
    SeedProduct(
        "ACC-007",
        "Chalk Ball 60 g",
        "Refillable mesh ball filled with magnesium carbonate, sealed so it dusts your "
        "hands without coating the gym floor. Lasts roughly two months of regular "
        "training. Ideal for pull-ups, deadlifts and bouldering.",
        9.00,
        ACCESSORIES,
        76,
        25,
        2,
    ),
    # ---------------- Electronics ----------------
    SeedProduct(
        "ELE-001",
        "GPS Running Watch Pace 2",
        "Dual-band GPS holds a lock between tall buildings and under tree cover, "
        "accurate to about 2 metres on a measured track. 26 hours of full GPS battery "
        "or 14 days in watch mode. The 1.3 inch always-on display stays readable in "
        "direct sun. Ideal for runners following a structured plan who need reliable "
        "splits. 5 ATM water resistant.",
        299.00,
        ELECTRONICS,
        22,
        10,
        5,
    ),
    SeedProduct(
        "ELE-002",
        "Bluetooth Earbuds",
        "Earbuds. Amazing quality.",
        79.00,
        ELECTRONICS,
        113,
        25,
        4,
    ),
    SeedProduct(
        "ELE-003",
        "Chest Strap Heart Rate Monitor",
        "Reads directly from the electrical signal of your heart rather than optically "
        "from the wrist, so it tracks intervals without lag. Broadcasts over both "
        "Bluetooth and ANT+ so it pairs with a watch and a bike computer at once. "
        "400 hours from a replaceable coin cell. Ideal for zone-based training.",
        69.00,
        ELECTRONICS,
        2,
        12,
        4,
    ),
    SeedProduct(
        "ELE-004",
        "Smart Scale",
        "Scale that measures weight.",
        59.00,
        ELECTRONICS,
        67,
        20,
        2,
    ),
    SeedProduct(
        "ELE-005",
        "Bike Computer Route 300",
        "A 2.6 inch transflective colour screen that gets easier to read as the sun "
        "gets brighter, with turn-by-turn navigation from routes synced over Wi-Fi. "
        "20 hours of battery with the map on. Mounts out front on a quarter-turn "
        "bracket. Ideal for long rides and unfamiliar roads.",
        219.00,
        ELECTRONICS,
        14,
        8,
        3,
    ),
    SeedProduct(
        "ELE-006",
        "Wireless Charging Pad",
        "Charges your phone.",
        29.00,
        ELECTRONICS,
        156,
        30,
        2,
    ),
    SeedProduct(
        "ELE-007",
        "Smart Jump Rope with Counter",
        "Counts jumps with a magnetic sensor in the handle and shows the total on an "
        "inline display, so you are not recounting after a miss. The 3 metre cable "
        "adjusts down to 2.4 metres in seconds. Rechargeable over USB-C, about six "
        "weeks per charge. Ideal for conditioning finishers and warm-ups.",
        39.00,
        ELECTRONICS,
        41,
        15,
        3,
    ),
]

assert len({p.sku for p in PRODUCTS}) == len(PRODUCTS), "duplicate SKU in seed data"
