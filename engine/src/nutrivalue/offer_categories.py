"""TASK-041: conservative offline Norwegian Tjek headline classification.

A category is a *search tag*, never SKU, EAN, nutrition, or checkout-price proof.
Adapted concepts (not copied code/data): the reviewed kundeavis-mcp Norwegian
whole-token dictionary and longest-specific-alias idea. No network or AI calls.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, replace
from functools import lru_cache

TAXONOMY_VERSION = 3
STATES = frozenset({"matched", "needs_review", "unmapped"})
CATEGORIES = frozenset({
    "fish", "meat", "dairy", "produce", "grain", "pantry",
    "frozen", "prepared", "drink", "non_food", "snacks", "sweets",
})
# All non-None keys below already exist in data/reference/food_roles.json.
# Deliberately *not* using mvt_search_terms: those are reference candidates,
# not reviewed retailer aliases. Unknown food types keep only a broad category.
# Rules are original, small, audited starter vocabulary: never treat a generic
# "kjøttdeig", "melk" or "laks" string as a specific retail SKU.
RULES = {
    "fish": (
        ("salmon", ("laks", "laksefilet", "laksefileter", "lakseskiver")),
        ("cod", ("torsk", "torskefilet", "torskefileter", "skrei")),
        ("saithe", ("sei", "seifilet", "seifileter")),
        ("tuna_drained", ("tunfisk avrent",)),
        (None, ("tunfisk", "tunfisk på boks", "tunfisk i olje")),
        ("mackerel_tomato", ("makrell i tomat",)),
        (None, ("makrell", "makrellfilet", "sild", "sildfilet", "lysing",
                "ørret", "ørretfilet", "røye", "kveite", "hyse",
                "fisk", "fiskefilet", "fiskefileter", "fiskekaker",
                "fiskepinner", "fiskegrateng", "fiskeboller",
                "fiskeburger", "fiskesuppe", "skalldyr", "blåskjell",
                "krabbe", "krabbeklør", "hummer", "sjøkreps")),
        ("shrimp", ("reker", "reker i løsvekt", "scampi")),
        ("sardines", ("sardiner", "sardiner på boks")),
    ),
    "meat": (
        ("chicken_breast", ("kyllingfilet", "kyllingfileter", "kyllingbryst")),
        ("chicken_thigh_fillet", ("kyllinglårfilet",)),
        ("chicken_drumstick", ("kyllingklubber", "kyllinglår med bein")),
        ("turkey_breast", ("kalkunfilet",)),
        ("turkey_mince", ("kalkunkjøttdeig",)),
        ("beef_mince", ("kjøttdeig av storfe", "storfe kjøttdeig")),
        ("lean_beef_mince", ("karbonadedeig",)),
        ("pork_mince", ("svinekjøttdeig", "kjøttdeig av svin")),
        ("pork_chops", ("svinekotelett", "svinekoteletter", "nakkekotelett")),
        ("pork_tenderloin", ("svinefilet", "svineindrefilet")),
        (None, ("kylling", "kalkun", "kjøttdeig", "svin", "storfe",
                "storfekjøtt", "biff", "entrecote", "oksekjøtt",
                "lammekjøtt", "lammelår", "lammekotelett",
                "pølser", "grillpølse", "bacon", "skinke",
                "kjøttboller", "kjøttkaker", "spareribs", "ribbe",
                "burger", "hamburger")),
    ),
    "dairy": (
        ("whole_eggs", ("egg", "hønseegg", "frilandsegg")),
        ("egg_whites", ("eggehvite", "eggehviter")),
        ("skyr", ("skyr",)),
        ("cottage_cheese", ("cottage cheese",)),
        ("mager_kesam", ("mager kesam", "mager kvarg")),
        ("milk", ("melk", "helmelk", "lettmelk", "skummet melk")),
        (None, ("yoghurt", "yogurt", "biola", "meierismør", "gulost", "hvitost",
                "cheddar", "jarlsberg", "norvegia", "brunost",
                "mozarella", "mozzarella", "feta", "kremost",
                "smør", "margarin", "rømme", "fløte", "kremfløte",
                "matfløte", "iskrem", "ispinne")),
    ),
    "produce": (
        (None, ("tomat", "tomater", "cherrytomater", "agurk",
                "paprika", "salat", "hjertesalat", "ruccola",
                "spinat", "brokkoli", "blomkål", "hvitkål",
                "spisskål", "gulrot", "gulrøtter", "potet",
                "poteter", "løk", "rødløk", "hvitløk",
                "sopp", "sjampinjong", "avokado", "banan",
                "bananer", "eple", "epler", "appelsin",
                "appelsiner", "clementiner", "sitron", "lime",
                "druer", "jordbær", "blåbær", "bringebær",
                "melon", "mango", "pære", "ananas", "kiwi", "asparges",
                "squash", "aubergine", "mais", "reddik", "purre")),
    ),
    "grain": (
        ("oats", ("havregryn",)),
        ("rice", ("ris", "jasminris", "basmatiris")),
        ("pasta", ("pasta", "spaghetti", "makaroni", "penne", "fusilli")),
        (None, ("brød", "grovbrød", "rundstykker", "loff",
                "baguette", "knekkebrød", "hvetemel", "mel",
                "tortilla", "tortillalefser", "wraps", "focaccia",
                "havreflak", "müsli", "sammalt", "speltmel", "grovbakstmel")),
    ),
    "pantry": (
        ("peanut_butter", ("peanøttsmør",)),
        ("red_lentils", ("røde linser",)),
        ("chickpeas", ("kikerter",)),
        ("beans", ("kidneybønner", "svarte bønner")),
        (None, ("linser", "bønner", "syltetøy", "sukker",
                "salt", "krydder", "ketchup", "majones",
                "sennep", "dressing", "saus", "tomatsaus",
                "tomatpure", "passata", "olivenolje",
                "rapsolje", "nøtter", "mandler", "honning",
                "hermetiske tomater")),
    ),
    "frozen": (
        (None, ("frossenpizza", "pommes frites", "potetbåter",
                "frosne grønnsaker", "frossen wokblanding")),
    ),
    "prepared": (
        (None, ("ferdigrett", "lasagne", "gryterett", "suppe",
                "pizza", "sushi", "pastasalat", "wrap", "vårruller")),
    ),
    "drink": (
        (None, ("kaffe", "teposer", "juice", "brus",
                "mineralvann", "saft", "energidrikk")),
    ),
    "snacks": (
        (None, ("müslibar", "proteinbar", "nøttebar", "potetgull", "chips", "popcorn", "ostepop", "osteringer",
                "nachips", "maiskaker", "salte kjeks", "snacks", "pretzel")),
    ),
    "sweets": (
        (None, ("sjokolade", "smågodt", "smågodteri", "godteri",
                "drops", "seigmenn", "lakris", "marsipan",
                "karameller", "godterier", "konfekt", "tyggis")),
    ),
    "non_food": (
        (None, ("fiskeolje", "tran", "omega 3", "omega-3",
                "kosttilskudd", "vitaminer", "hundemat",
                "kattemat", "dyrefôr", "fiskefôr", "toalettpapir",
                "vaskemiddel", "tannkrem", "sjampo", "shampoo",
                "såpe", "oppvaskmiddel", "bleier")),
    ),
}
# V2: HIGH-PRECISION primary PRODUCT FAMILY cues. Unlike ingredients, these
# indicate what the sold item *is*. Earlier V1 misclassified fruit-flavored
# drinks as produce, cheese-flavored snacks as dairy, and fish-flavored cat
# treats as fish. These cues are scoped to explicit product types/known
# unambiguous brands, not ad copy, ingredient lists or a free-form AI guess.
# Priority is intentional: pet/household -> snack/sweet -> dairy/drink ->
# cooked/prepared -> other ingredient classes. No food_key ever inferred.
# Tuples: (category, form, aliases). All returned keys must be in CATEGORIES.
PRODUCT_FAMILIES = (
    ("non_food", "not_applicable", (
        "sheba", "dreamies", "latz", "pedigree", "dyremat",
        "hundesnacks", "kattemat", "hundemat",
        "blomsterbukett", "bukett", "favorittbukett", "ildtopp", "roser", "tulipaner", "blomstermiks",
        "calluna", "høstlyng", "blomster", "rosebukett",
        "oppvaskbørste", "tørkerull", "tørkeruller", "tørkepapir",
        "håndsåpe", "dusjsåpe", "intimservietter",
        "truseinnlegg", "balsam", "lypsyl", "leppepomade",
        "deodorant", "roll on", "roll-on", "body spray", "bodyspray",
        "body face", "hårfarge", "tøymykner", "vaskekapsler", "vask og tilbehør", "personlig pleie", "torky", "oppvask",
        "kjøkkenspray", "allrent", "klor", "avløpsåpner", "plumbo",
        "lambi", "omo", "zalo", "biotex", "softlan", "ajax",
        "sensodyne", "grønnsåpe",
        "libresse", "libero", "nivea", "apobase", "tena", "dagkrem", "cleansing", "genser", "carmex", "listerine",
        "sunsilk", "colgate", "sokker", "ullpledd", "lyspære",
        "fyrstikker", "halloweenbøtte", "ullsokker", "leketøy", "lego", "oppbevaringsboks",
        "handlenett", "glidelåsposer", "bomullspads", "hudkrem",
    )),
    ("frozen", "frozen", (
        "sørlandsis", "diplom is", "diplom fløteis", "fløteis",
        "krone is", "liter is", "iskrem", "ispinner",
    )),
    ("snacks", "processed", (
        "ostepop", "osteringer", "potetgull", "chips",
        "sørlandschips", "pringles", "poppa chips",
        "cheese doodles", "nachips", "popcorn", "maiskaker",
        "saltstenger", "pretzel", "sørlands chips",
        "kims", "olw", "maarud", "tynn sprø", "salte kjeks",
        "ritz kjeks", "tuc kjeks",
    )),
    ("sweets", "processed", (
        "sjokolade", "melkesjokolade", "sjokobiter", "sjokomelkebar", "sjokoladebar",
        "storplater", "småsjokolade", "småsjokolader",
        "smågodt", "smågodteri", "smågodterier", "smågodtkopp", "godteri", "kosegodt", "candy",
        "karamell", "caramels", "marsipan", "konfekt", "drops",
        "pastiller", "tyggis", "lakris", "krokanrull",
        "kinder", "kinderegg", "gullbrød", "japp", "troika", "skittles", "nidar",
        "freia", "haribo", "fazer", "werthers", "ricola",
        "lækerol", "läkerol", "dent", "seigmenn", "smash",
        "kvikk lunsj", "kitkat", "kex", "bixit mini",
        "tyggegummi", "hubba bubba", "stimorol", "knatter", "juicy drop", "kjærlighet", "halslinser", "fisherman s friend",
        "smil chewies", "muffins", "sjokoladepudding",
        "karamellpudding", "tiramisù", "tiramisu", "donuts", "vaniljeskilling",
    )),
    ("dairy", "processed", (
        "kesam", "drikkeyoghurt", "danonino", "litago", "yoghurt", "yogurt", "kvarg", "kefir", "apetina", "blå viking", "soft flora", "parmigiano", "sjokomelk",
        "sjokolademelk", "fløtemysost", "julebrunost",
        "revet ost", "lettost", "gruyere", "gulost", "norvegia",
        "philadelphia", "kremgo", "kremgo'", "prim original",
        "prim lett", "crème fraîche", "creme fraiche",
        "frokostegg",
    )),
    ("drink", "unspecified", (
        "coca cola", "coca-cola", "pepsi", "pepsi max", "fanta", "powerking white",
        "solo", "urge", "monster", "burn", "red bull",
        "battery", "powerade", "powerking", "gatorade",
        "fun light", "smoothie", "smoothies", "havredrikk", "sprudle", "protein shake", "iste",
        "te", "black tea", "fuzetea", "teposer",
        "fruitdrikk", "fruktdrikk", "eplemost", "fruktjuice",
        "sjokoladedrikk", "kullsyredrikk",
        "sukkerfri brus", "clausthaler", "munkholm",
        "farris", "imsdal", "san pellegrino",
        "kondi", "starbucks", "espresso",
        "filterkaffe", "filtermalt", "kokmalt", "frokostkaffe", "kaffe",
        "saft", "vann", "solbærtoddy", "energidrikk",
    )),
    ("prepared", "processed", (
        "poke bowl", "pasta snack", "rett i koppen",
        "sweet sour", "marry me chicken", "grandiosa", "big one", "kyllinggryte", "bali kyllinggryte",
        "supper", "vårrull", "pan pizza",
        "lasagne", "pizzaslice", "pizza slice", "ristorante",
        "ferdigretter", "børek", "gryte", "nuggets",
        "ostestenger", "ostesticks", "løkringer", "mozzarella sticks",
        "osteringer", # shadowed by snacks above
        "chili cheese nuggets", "risotto kremet",
    )),
    ("grain", "processed", (
        "croissant", "veggen spelt", "skolebrød", "skillingsbolle", "solbolle",
        "rundstykke", "rundstykker", "tortillas", "osterundstykke", "spesialloff", "bakevarer", "morgenstykker",
        "polarbrød", "superbrød", "bjørnisbrød", "solsikkebrød",
        "kornbrød", "speltbrød", "havrebrød", "flerkornbrød",
        "tagliatelle", "fettuccine", "tortelloni", "tortellini",
        "fullkornpasta", "gnocchi", "pizzadeig", "pannekaker",
        "frokostblanding", "gifflar", "myk og rund", "nudler", "breakfast rolls", "granola", "bygglunsj", "havrelunsj", "cornflakes", "cheerios",
        "müsli", "havregrøt", "byggrynsgrøt", "risgrøt",
        "boller", "bolle", "skolebrødstang",
    )),
    ("meat", "processed", (
        "kryddersalami", "gullsalami", "salami", "mortadella",
        "pancetta", "prosciutto", "prociutto", "pulled pork",
        "leverpostei", "baconpostei", "stjernebacon", "baconterninger",
        "kalkunkorv", "falukorv", "kjøttpølse", "koteletter",
        "nakkekoteletter", "lårfilet", "kyllingkjøttdeig",
        "kyllingnuggets", "kyllingbacon", "svinekjøtt",
        "kyllinglår", "kyllingilet", "baconburger", "svineknoke", "svinekam", "lammekjøtt",
        "lammebog", "grytekjøtt", "fårikålkjøtt", "karbonader",
        "smårettskinke", "kalkunpålegg",
        "kyllingpålegg", "pinnekjøtt", "kjøttbuljong",
        "reinsdyrskank", "reinsdyrkjøtt", "elgstek",
        "finnbiff", "viltkjøtt", "kyllingvinger", "lollipopvinger",
    )),
    ("fish", "processed", (
        "sprøbakt fisk", "sprøbakt torsk", "hjertefiskekaker", "fiskeburger",
        "lakseburger", "fiskepudding", "hysekaker",
        "fiskekaker", "fiskepinner", "fiskegrateng",
        "fiskeboller", "bacalao", "bacalaofisk", "sprøbakt lysing", "fish crisp",
        "fish & crisp", "lobnobs",
    )),
    ("produce", "unspecified", (
        "kiwi", "koriander", "ruccula", "ruccola", "isberg", "middelhavsmiks", "begersalat", "lapskausblanding", "avocado", "søtpotet", "søtpoteter", "granateple", "plommer",
        "klasetomat", "cherrytomat", "hverdagstomater",
        "crispisalat", "favorittsalat", "isbergmiks", "middelhavssalat", "spisspaprika", "rød chili",
        "sødmetomater", "brokkoliblanding",
        "rotgrønnsaker", "salatbar", "grønnkål",
        "aspargesbønner", "bringebær", "blåbær",
    )),
    ("pantry", "processed", (
        "sauser til", "saus til", "pastasaus", "pizzasaus", "pizzaolje", "arrabbiata", "kaviarmix", "jordbærsyltetøy", "pesto",
        "bearnaisesaus", "remulade", "dipp", "buljong",
        "tomatpure", "tomatpuré", "chilinøtter",
        "kaviar", "akasiehonning",
        "honning", "nøttemiks", "solsikkeolje", "rapsolje",
        "sesamfrø", "chiafrø", "gresskarkjerner",
        "solsikkekjerner", "linfrø", "kryddermiks", "pastasaus",
        "tandoori", "tikka masala", "sopp saus",
        "maiskorn", "kikerter", "linser",
    )),
)

@lru_cache(maxsize=4096)
def _family_result(norm: str):
    # The earliest sold noun wins over an unmarked flavour later in the name;
    # at the same position the longest reviewed phrase is more specific.
    candidates = []
    for order, (category, form, aliases) in enumerate(PRODUCT_FAMILIES):
        for alias in aliases:
            phrase = _normal(alias)
            match = re.search(r"(?<!\S)" + re.escape(phrase) + r"(?!\S)", norm)
            if match:
                candidates.append((match.start(), -len(phrase), order, category, form))
    if not candidates:
        return None
    if any(item[3] == "non_food" for item in candidates):
        candidates = [item for item in candidates if item[3] == "non_food"]
    elif any(item[3] != "produce" for item in candidates):
        candidates = [item for item in candidates if item[3] != "produce"]
    *_, category, form = min(candidates)
    return category, form


# Mark an ingredient form that cannot safely be mapped to a generic *raw*
# food-key nutrient basis, even when the species is in the headline.
FORM_REVIEW = (
    "røkt", "røyket", "gravet", "gravlaks", "panert", "marinert",
    "grillet", "stekt", "kokt", "pålegg", "fiskekaker", "fiskeboller",
    "fiskepinner", "fiskegrateng", "fiskeburger", "fiskesuppe",
    "krydret", "kryddermarinert", "sushi", "spiseklar", "sprøbakt",
    "frityrstekt", "langtidskokt", "crispy",
)
FROZEN = ("frossen", "frosne", "fryst", "fryste", "dypfryst")
PLANT_IMITATION = ("vegansk", "vegetar", "plantebasert", "vegan")
WORD = re.compile(r"[^\W_]+", re.UNICODE)


@dataclass(frozen=True)
class Classification:
    status: str
    food_category: str | None
    food_key: str | None
    form: str
    reason: str
    taxonomy_version: int = TAXONOMY_VERSION
    category_tags: tuple[str, ...] = ()
    offer_kind: str = "unknown"
    tag_status: str = "unresolved"

    def __post_init__(self):
        tags = self.category_tags or ((self.food_category,) if self.food_category else ())
        if set(tags) - CATEGORIES:
            raise ValueError("unknown category tag")
        object.__setattr__(self, "category_tags", tuple(sorted(set(tags))))
        if self.food_category and self.tag_status == "unresolved":
            candidate = self.reason in {"description_only", "description_disambiguation"}
            object.__setattr__(self, "tag_status", "candidate" if candidate else "complete")
            if self.offer_kind == "unknown" and not candidate:
                object.__setattr__(self, "offer_kind", "single_product")

    @property
    def food_analysis_eligible(self) -> bool:
        """Category-only food filter; never nutrition/price rank approval."""
        return (self.offer_kind == "single_product" and self.tag_status == "complete"
                and self.food_category in CATEGORIES - {"non_food"}
                and self.reason not in {"description_only", "multiple_categories",
                                        "mixed_product_selection",
                                        "description_disambiguation"})


def _normal(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("source text must be a string")
    value = unicodedata.normalize("NFKC", value).casefold()
    value = re.sub(r"\bm/", " med ", value)
    value = re.sub(r"\bu/", " uten ", value)
    return " ".join(WORD.findall(value))


def _present(text: str, phrase: str) -> bool:
    return (" " + phrase + " ") in (" " + text + " ")


@lru_cache(maxsize=4096)
def _hits(text: str) -> list[tuple[str, str | None, int]]:
    # Keep positions: a longer named prepared food (MAKRELL I TOMAT)
    # includes words that would otherwise be classified independently
    # (MAKRELL as fish; TOMAT as produce). Only suppress shorter matches
    # INSIDE the longer phrase, never a separate ingredient elsewhere.
    candidates = []
    for category, groups in RULES.items():
        for food_key, aliases in groups:
            for alias in aliases:
                normal_alias = _normal(alias)
                pattern = r"(?<!\S)" + re.escape(normal_alias) + r"(?!\S)"
                for match in re.finditer(pattern, text):
                    candidates.append((match.start(), match.end(),
                                       category, food_key))
    found = []
    for start, end, category, food_key in candidates:
        if any(other_start <= start and end <= other_end
               and other_end - other_start > end - start
               for other_start, other_end, _, _ in candidates):
            continue
        found.append((category, food_key, end - start))
    return found


# Product heads and productive Norwegian noun compounds, not offer IDs/brands.
# Suffix matching is bounded to reviewed compound heads; no arbitrary stemming.
# Prepared potato products and ice cream retain the historical frozen search tag;
# raw frozen fruit/vegetables retain produce with a frozen form.
KIND_HEADS = (
    ("non_food", "not_applicable", (
        "sensodyne", "tannkrem", "såpe", "grønnsåpe", "vaskesåpe",
        "sokk", "sokker", "genser", "serveringsbeger", "brødkniv",
        "såpedispenser", "emballasje", "fryseposer",
    )),
    ("frozen", "frozen", (
        "iskrem", "fløteis", "sørlandsis", "diplom is", "krone is",
        "liter is", "ispinner", "opphøgde potteter", "opphogde poteter",
        "pommes frites", "potetbåter",
    )),
    ("pantry", "processed", (
        "potetmos", "buljong", "kjøttbuljong", "grønnsaksbuljong",
        "sauser", "sausposer", "suppeposer", "pastasaus", "tomatsaus",
        "pizzasaus", "majones", "hakkede tomater", "hermetiske tomater",
        "tomater hakkede", "passata", "tomatpure", "tomatpuré",
    )),
    ("prepared", "processed", (
        "fårikål", "viltgryte", "bacalao", "pai", "vårull", "vårrull",
        "pizza", "pizzabolle", "tacowrap", "ferdigrett", "ferdigretter",
    )),
    ("fish", "processed", (
        "tunfisk", "makrellfilet i tomat", "fiskesuppe", "fiskekake",
    )),
    ("meat", "processed", (
        "spekeskinke", "saltkjøtt", "pølse", "burgere", "karbonader",
        "stekte kjøttkaker", "rein medaljong", "kjøttkake", "kjøttbrød",
    )),
    ("drink", "unspecified", ("kaffedrikk", "kaffebønner")),
    ("produce", "unspecified", ("grønnsaker", "smoothiemix", "smoothiemiks")),
    ("sweets", "processed", ("kjeks", "kake", "kaker")),
)
COMPOUND_HEADS = {
    "non_food": ("såpe", "genser", "sokk", "sokker"),
    "grain": ("brød",),
    "produce": ("poteter",),
    "pantry": ("majones", "sauser", "buljong"),
    "sweets": ("kake", "kjeks", "pastiller"),
    "meat": ("spekeskinke", "pølse"),
}


@lru_cache(maxsize=4096)
def _kind_result(norm: str):
    if _present(norm, "kjøttboller"):
        return "meat", "processed"
    # Broad salty-biscuit cues are more specific than generic biscuit heads.
    if any(_present(norm, w) for w in ("salte kjeks", "ritz kjeks", "tuc kjeks")):
        return "snacks", "processed"
    for category, form, aliases in KIND_HEADS:
        if any(_present(norm, alias) for alias in aliases):
            return category, form
    for token in norm.split():
        for category, suffixes in COMPOUND_HEADS.items():
            if any(token.endswith(suffix) and len(token) > len(suffix)
                   for suffix in suffixes):
                return category, ("not_applicable" if category == "non_food"
                                  else "unspecified")
    return None


@lru_cache(maxsize=4096)
def _primary_result(norm: str):
    if " med " in norm:
        head = norm.split(" med ", 1)[0]
        primary_head = _primary_result(head)
        if primary_head:
            return primary_head
    family = _family_result(norm)
    if family and family[0] == "non_food":
        return family
    kind = _kind_result(norm)
    if family and family[0] == "dairy" and kind == ("drink", "unspecified"):
        return family
    if family and kind and not any(
            _present(norm, alias) for _, _, aliases in KIND_HEADS for alias in aliases):
        return family
    return kind or family


def _selection_category(norm: str):
    primary = _primary_result(norm)
    if primary:
        return primary[0]
    if any(_present(norm, word) for word in ("müslibar", "proteinbar", "nøttebar")):
        return "snacks"
    return None


FLAVOURS = frozenset({
    "jordbær", "bringebær", "blåbær", "eple", "pære", "paprika", "mango",
    "vanilje", "vaniljesmak", "sjokolade", "salt", "havsalt", "pepper",
    "naturell", "original", "lett", "sukker", "chili", "karamell",
    "honning", "gressløk", "melk", "olivenolje", "pekan", "salted", "caramel",
    "light", "hvitløk", "fersken", "sitron", "mandel", "purre", "dressing",
    "grovrevet", "i", "tomat", "thai", "msc",
})
QUALIFIERS = frozenset({"hele", "hakkede", "skåret", "oppskåret", "fryst", "frossen"})


def _overlap_tags(category: str, norm: str) -> set[str]:
    # Qualifiers after MED describe an ingredient, not the sold item.
    head = norm.split(" med ", 1)[0]
    tags = {category}
    if category != "non_food" and any(_present(head, word) for word in FROZEN):
        tags.add("frozen")
    if category == "dairy" and any(_present(head, word) for word in (
            "drikkeyoghurt", "sjokomelk", "sjokolademelk", "melk", "helmelk",
            "lettmelk", "skummet melk", "litago")):
        tags.add("drink")
    return tags


@lru_cache(maxsize=4096)
def _selection_info(text: str):
    """Recognize independently named items, preserving flavour continuations."""
    text = re.sub(r"\bm/", " med ", text.casefold())
    text = re.sub(r"\bu/", " uten ", text)
    text = re.sub(r"\bstekte kaker\b", "stekte kjøttkaker", text)
    # Coordinated truncated compounds share the final product head.
    if re.search(r"-\s+og\s+", text) and _primary_result(_normal(text)):
        return None
    # An explicit PLUS separates bundle components; '+ pant' is a deposit.
    groups = re.split(r"\s+\+\s+", text)
    groups = [group for group in groups if _normal(group) != "pant"]
    kind = "bundle" if len(groups) > 1 else "assortment"
    # In coordinated stock names the final noun is the product head:
    # chicken, fish and vegetable STOCK are not three independently sold foods.
    if len(groups) == 1 and "/" not in text and any(
            token.endswith("buljong") for token in _normal(text).split()):
        return None
    components = []
    for group in groups:
        group = group.split(" med ", 1)[0]
        parts = re.split(r"/|\s+og\s+|(?<!\d),(?!\d)", group)
        parent = None
        flavour_context = False
        for part in parts:
            norm = _normal(part)
            if (not norm or norm in QUALIFIERS or norm == "pant"
                    or re.fullmatch(r"[\d ]+(?:g|kg|ml|l|pk|stk)?", norm)):
                continue
            primary = _primary_result(norm)
            cats = {hit[0] for hit in _hits(norm)}
            category = primary[0] if primary else (
                next(iter(cats)) if len(cats) == 1 else None)
            flavour_only = set(norm.split()) <= FLAVOURS
            if parent and flavour_context and flavour_only:
                continue
            # Species/vegetable names after a pet food are flavours unless a
            # PLUS explicitly starts a new independent component group.
            if parent == "non_food" and category in {"fish", "meat", "produce"}:
                continue
            components.append((category, norm))
            if category:
                parent = category
                flavour_context = category in {"dairy", "drink", "sweets", "snacks", "grain", "prepared", "frozen", "pantry", "fish"} and primary is not None and bool(set(norm.split()) & FLAVOURS)
    recognized = sum(cat is not None for cat, _ in components)
    if (len(components) < 2 or recognized == 0
            or (recognized < 2 and not (text.count("/") >= 2 or len(groups) > 1))):
        return None
    tags = set()
    for category, norm in components:
        if category:
            tags.update(_overlap_tags(category, norm))
    base_categories = {cat for cat, _ in components if cat}
    complete = all(cat for cat, _ in components)
    return tuple(sorted(tags)), base_categories, complete, kind


def _mixed_selection(text: str) -> bool:
    info = _selection_info(text)
    return bool(info and (len(info[1]) > 1 or not info[2]))


def _selection_result(text: str) -> Classification:
    tags, _, complete, kind = _selection_info(text)
    return Classification("needs_review", None, None, "mixed",
                          "mixed_product_selection", category_tags=tags,
                          offer_kind=kind, tag_status="complete" if complete else "partial")


def _classify_offer(heading: str, description: str | None = None) -> Classification:
    """Categorize a flyer offer without implying SKU or nutrition identity.

    Description matches are review-only: descriptions can contain examples,
    cross-selling and price units, unlike a primary heading.
    """
    norm = _normal(heading)
    if not norm or len(norm) > 400:
        raise ValueError("offer heading missing or too long")
    if description is not None and (not isinstance(description, str)
                                    or len(description) > 4000):
        raise ValueError("invalid offer description")
    # Non-food/feed evidence must be checked before any food-family or
    # beverage-volume shortcut. Pet species are flavours, never human food.
    nonfood_hit = any(hit[0] == "non_food" for hit in _hits(norm))
    primary = _primary_result(norm)
    pet = any(_present(norm, word) for word in (
        "sheba", "dreamies", "latz", "pedigree", "hundemat", "kattemat",
        "dyrefôr", "fiskefôr", "dyremat", "hundesnacks"))
    if pet and _mixed_selection(heading):
        return _selection_result(heading)
    if pet:
        return Classification("matched", "non_food", None, "not_applicable",
                              "explicit_non_food")
    if nonfood_hit or (primary and primary[0] == "non_food"):
        if _mixed_selection(heading):
            return _selection_result(heading)
        return Classification("matched", "non_food", None, "not_applicable",
                              "explicit_non_food")
    if " med " in norm:
        head = norm.split(" med ", 1)[0]
        head_primary = _primary_result(head)
        head_categories = {hit[0] for hit in _hits(head)}
        full_primary = _primary_result(norm)
        if (head_primary is None and head_categories and full_primary
                and full_primary[0] not in head_categories):
            return Classification("needs_review", None, None, "mixed",
                                  "multiple_categories")
    # Fix the conventional cooked-meat noun phrase before bakery matching.
    norm = norm.replace("stekte kaker", "stekte kjøttkaker")
    if _present(norm, "donut burger"):
        return Classification("needs_review", None, None, "mixed",
                              "ambiguous_product_kind")
    if _present(norm, "wraps") and description:
        descriptor = _normal(description)
        if any(_present(descriptor, w) for w in ("tacowrap", "fajitas kylling")):
            return Classification("needs_review", "prepared", None, "processed",
                                  "description_disambiguation")
    if _present(norm, "drikke") and not _family_result(norm):
        if description and _mixed_selection(description):
            candidate = _selection_result(description)
            if candidate.tag_status == "complete":
                return candidate
        return Classification("needs_review", "drink", None, "unspecified",
                              "broad_beverage_selection", offer_kind="assortment")
    if _present(norm, "salmiakk"):
        if re.search(r"\b\d+(?:[.,]\d+)?\s*(?:ml|l)\b", norm):
            return Classification("matched", "non_food", None, "not_applicable",
                                  "salmiakk_liquid_basis")
        if description and any(_present(_normal(description), word) for word in
                               ("rengjøring", "rengjøringsmiddel", "vaskemiddel")):
            return Classification("matched", "non_food", None, "not_applicable",
                                  "explicit_cleaning_context")
        return Classification("needs_review", None, None, "unknown",
                              "ambiguous_product_kind")
    if norm.startswith("mer "):
        # MER is also an ordinary Norwegian word. Without a beverage basis,
        # fruit words alone cannot establish whether this is juice or fruit.
        if description and re.search(r"\b\d+(?:[.,]\d+)?\s*(?:ml|l)\b",
                                     description.casefold()):
            return Classification("matched", "drink", None, "unspecified",
                                  "beverage_brand_and_volume")
        return Classification("needs_review", None, None, "unknown",
                              "ambiguous_product_kind")
    # Whole coffee beans are a drink ingredient, not legumes. A generic beans
    # offer is unchanged; the phrase requires an explicit coffee producer cue.
    if _present(norm, "hele bønner") and _present(norm, "ali"):
        return Classification("matched", "drink", None, "unspecified",
                              "coffee_beans_context")
    if _mixed_selection(heading.replace("STEKTE KAKER", "STEKTE KJØTTKAKER").replace("stekte kaker", "stekte kjøttkaker")):
        return _selection_result(heading)
    # The sold product family takes precedence over incidental flavor words.
    # Never use a source description or model to auto-approve this override.
    family = _primary_result(norm)
    if family is not None:
        category, form = family
        if category == "non_food":
            return Classification("matched", "non_food", None,
                                  "not_applicable", "product_family_non_food")
        if any(_present(norm, _normal(w)) for w in PLANT_IMITATION) and category in ("meat", "fish", "dairy"):
            return Classification("needs_review", None, None, "substitute",
                                  "plant_based_imitation")
        if category == "fish" or any(_present(norm, _normal(w)) for w in FORM_REVIEW):
            return Classification("needs_review", category, None, "processed",
                                  "preparation_or_form")
        # Meat labels include fresh cuts and cured/ready-to-eat foods:
        # do not label raw lamb/pork/chicken automatically processed.
        if category == "meat":
            processed_meat = (
                "salami", "mortadella", "pancetta", "prosciutto",
                "prociutto", "pulled pork", "leverpostei", "bacon",
                "pølse", "korv", "pålegg", "karbonader", "nuggets", "pinnekjøtt",
            )
            form = ("processed" if any(piece in norm for piece in processed_meat)
                    else "unspecified")
        if form == "unspecified" and any(_present(norm.split(" med ", 1)[0], w) for w in FROZEN):
            form = "frozen"
        return Classification("matched", category, None, form,
                              "product_family_cue")
    hits = _hits(norm)
    secondary = False
    if not hits and description:
        if _mixed_selection(description):
            return _selection_result(description)
        hits = _hits(_normal(description))
        secondary = bool(hits)
    if not hits:
        return Classification("unmapped", None, None, "unknown", "no_safe_alias")

    cats = {x[0] for x in hits}
    # An explicit pet food/supplement cue overrides species words in headings,
    # rather than letting "LAKS KATTEMAT" enter a fish shopping search.
    if "non_food" in cats:
        return Classification("matched", "non_food", None, "not_applicable",
                              "explicit_non_food")
    if len(cats) != 1:
        return Classification("needs_review", None, None, "mixed",
                              "multiple_categories")
    category = next(iter(cats))
    form = "frozen" if any(_present(norm.split(" med ", 1)[0], _normal(w)) for w in FROZEN) else "unspecified"
    keys = {key for _, key, _ in hits if key is not None}
    if len(keys) > 1:
        return Classification("needs_review", category, None, "mixed",
                              "multiple_food_keys")
    if secondary:
        # Fruit/botanical flavour lists are common in desserts and cosmetics;
        # an unknown sold name cannot be established as raw produce by those.
        return Classification("needs_review", None if category == "produce" else category, None, form,
                              "description_only")
    if any(_present(norm, _normal(w)) for w in PLANT_IMITATION):
        return Classification("needs_review", None, None, "substitute",
                              "plant_based_imitation")
    if any(_present(norm, _normal(w)) for w in FORM_REVIEW):
        return Classification("needs_review", category, None, "processed",
                              "preparation_or_form")
    # Names represent only a possible food TYPE, never a reviewed exact SKU.
    food_key = next(iter(keys), None)
    return Classification("matched", category, food_key, form,
                          "known_food_type" if food_key else "broad_category")


def classify_offer(heading: str, description: str | None = None) -> Classification:
    """Broad search tags; a singular compatibility view when supported.

    Storage and drinkable dairy may overlap with product-kind tags. Separate
    products never gain a joint food key or analytical denominator from tags.
    """
    result = _classify_offer(heading, description)
    info = _selection_info(heading)
    if (info and result.tag_status == "complete"
            and (result.food_category is None or result.food_category in info[1])):
        tags, _, complete, kind = info
        return replace(result, food_key=None, category_tags=tags,
                       offer_kind=kind, tag_status="complete" if complete else "partial")
    if result.offer_kind != "single_product" or result.tag_status != "complete":
        return result
    tags = set(result.category_tags)
    tags.update(_overlap_tags(result.food_category, _normal(heading)))
    if result.form == "frozen" and result.food_category != "non_food":
        tags.add("frozen")
    return replace(result, category_tags=tuple(sorted(tags)))
