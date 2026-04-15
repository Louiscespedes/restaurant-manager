"""
Bilingual Food Dictionary — Swedish/English translation for smart search.
Maps common food terms between Swedish and English so restaurant managers
can search in either language and find matching products.
"""

# Swedish -> English and English -> Swedish food mappings
# Each entry: { "sv": ["swedish terms"], "en": ["english terms"] }
FOOD_DICTIONARY = [
    # Proteins - Meat
    {"sv": ["nötkött", "nöt", "oxkött", "ox", "entrecote", "ryggbiff", "fransyska", "högrev", "innanlår", "rostbiff"], "en": ["beef", "ox", "sirloin", "ribeye", "chuck", "round"]},
    {"sv": ["fläsk", "fläskkött", "gris", "griskött", "kotlett", "fläskfilé", "bacon", "skinka", "karré"], "en": ["pork", "pig", "chop", "ham", "bacon", "tenderloin"]},
    {"sv": ["kyckling", "kycklingbröst", "kycklinglår", "kycklingvingar"], "en": ["chicken", "chicken breast", "chicken thigh", "chicken wing"]},
    {"sv": ["lamm", "lammkött", "lammkotlett", "lammstek", "lammfärs"], "en": ["lamb", "lamb chop", "lamb roast"]},
    {"sv": ["vilt", "viltkött", "hjort", "rådjur", "älg", "vildsvin", "ren", "renskav"], "en": ["game", "venison", "deer", "moose", "elk", "wild boar", "reindeer"]},
    {"sv": ["anka", "ankbröst", "anklår"], "en": ["duck", "duck breast"]},
    {"sv": ["kalkon"], "en": ["turkey"]},
    {"sv": ["kalv", "kalvkött", "kalvfilé", "kalvschnitzel"], "en": ["veal"]},
    {"sv": ["korv", "prinskorv", "bratwurst", "chorizo", "salami"], "en": ["sausage", "bratwurst", "chorizo", "salami"]},
    {"sv": ["färs", "nötfärs", "blandfärs", "köttfärs", "fläskfärs"], "en": ["ground meat", "mince", "minced meat", "ground beef"]},

    # Proteins - Seafood
    {"sv": ["lax", "gravlax", "rökt lax", "laxfilé"], "en": ["salmon", "smoked salmon", "gravlax"]},
    {"sv": ["torsk", "torskfilé", "torskrygg"], "en": ["cod", "codfish"]},
    {"sv": ["räkor", "räka", "jätteräkor", "handskalade räkor"], "en": ["shrimp", "prawn", "prawns", "shrimps"]},
    {"sv": ["tonfisk", "tonfiskbuk"], "en": ["tuna"]},
    {"sv": ["sill", "matjessill", "inlagd sill", "strömming"], "en": ["herring", "pickled herring", "baltic herring"]},
    {"sv": ["hummer"], "en": ["lobster"]},
    {"sv": ["krabba", "krabba", "krabbkött"], "en": ["crab"]},
    {"sv": ["musslor", "blåmusslor"], "en": ["mussels", "blue mussels"]},
    {"sv": ["bläckfisk", "calamari", "tioarmad bläckfisk"], "en": ["squid", "calamari", "octopus"]},
    {"sv": ["rödspätta", "sjötunga", "piggvar", "hälleflundra"], "en": ["plaice", "sole", "turbot", "halibut", "flatfish"]},
    {"sv": ["abborre", "gös", "gädda", "öring"], "en": ["perch", "pike-perch", "pike", "trout"]},

    # Dairy
    {"sv": ["mjölk", "helmjölk", "lättmjölk", "mellanmjölk"], "en": ["milk", "whole milk", "skim milk"]},
    {"sv": ["smör", "matlagningssmör"], "en": ["butter"]},
    {"sv": ["grädde", "vispgrädde", "matlagningsgrädde", "creme fraiche"], "en": ["cream", "whipping cream", "cooking cream", "creme fraiche"]},
    {"sv": ["ost", "cheddar", "parmesan", "mozzarella", "brie", "gorgonzola", "västerbottenost", "prästost", "herrgårdsost"], "en": ["cheese", "cheddar", "parmesan", "mozzarella", "brie", "gorgonzola"]},
    {"sv": ["ägg", "hönsägg"], "en": ["egg", "eggs"]},
    {"sv": ["yoghurt", "grekisk yoghurt", "turkisk yoghurt", "kvarg"], "en": ["yogurt", "greek yogurt", "quark"]},
    {"sv": ["burrata", "ricotta", "mascarpone", "halloumi", "fetaost"], "en": ["burrata", "ricotta", "mascarpone", "halloumi", "feta"]},

    # Vegetables
    {"sv": ["tomat", "tomater", "körsbärstomat", "soltorkade tomater"], "en": ["tomato", "tomatoes", "cherry tomato", "sun-dried tomato"]},
    {"sv": ["lök", "rödlök", "gul lök", "sallads lök", "vitlök", "purjolök", "schalottenlök"], "en": ["onion", "red onion", "yellow onion", "spring onion", "garlic", "leek", "shallot"]},
    {"sv": ["potatis", "färskpotatis", "bakpotatis", "sötpotatis"], "en": ["potato", "potatoes", "new potato", "baked potato", "sweet potato"]},
    {"sv": ["morot", "morötter"], "en": ["carrot", "carrots"]},
    {"sv": ["paprika", "röd paprika", "grön paprika", "gul paprika"], "en": ["bell pepper", "pepper", "capsicum"]},
    {"sv": ["gurka"], "en": ["cucumber"]},
    {"sv": ["sallad", "isbergssallad", "rucola", "romansallad", "babyspenat"], "en": ["lettuce", "salad", "iceberg", "arugula", "rocket", "romaine", "baby spinach"]},
    {"sv": ["spenat"], "en": ["spinach"]},
    {"sv": ["broccoli"], "en": ["broccoli"]},
    {"sv": ["blomkål"], "en": ["cauliflower"]},
    {"sv": ["zucchini", "squash"], "en": ["zucchini", "courgette", "squash"]},
    {"sv": ["aubergine"], "en": ["eggplant", "aubergine"]},
    {"sv": ["svamp", "champinjoner", "karl johan", "kantareller", "shiitake", "portobello"], "en": ["mushroom", "mushrooms", "champignon", "chanterelle", "shiitake", "portobello"]},
    {"sv": ["selleri", "rotselleri"], "en": ["celery", "celeriac"]},
    {"sv": ["kål", "vitkål", "rödkål", "grönkål", "spetskål"], "en": ["cabbage", "white cabbage", "red cabbage", "kale", "pointed cabbage"]},
    {"sv": ["bönor", "vita bönor", "kidneybönor", "gröna bönor", "haricots verts"], "en": ["beans", "white beans", "kidney beans", "green beans", "haricots verts"]},
    {"sv": ["ärtor", "sockerärtor", "frysta ärtor"], "en": ["peas", "sugar snap peas", "frozen peas"]},
    {"sv": ["majs", "majskolv"], "en": ["corn", "sweet corn", "corn on the cob"]},
    {"sv": ["sparris", "grön sparris", "vit sparris"], "en": ["asparagus"]},
    {"sv": ["kronärtskocka", "jordärtskocka"], "en": ["artichoke", "jerusalem artichoke"]},
    {"sv": ["betor", "rödbetor"], "en": ["beet", "beetroot"]},
    {"sv": ["rädisor"], "en": ["radish", "radishes"]},
    {"sv": ["palsternacka"], "en": ["parsnip"]},
    {"sv": ["fänkål"], "en": ["fennel"]},
    {"sv": ["avokado"], "en": ["avocado"]},

    # Fruits
    {"sv": ["äpple", "äpplen"], "en": ["apple", "apples"]},
    {"sv": ["citron", "lime"], "en": ["lemon", "lime"]},
    {"sv": ["apelsin", "blodapelsin", "mandarin", "clementin"], "en": ["orange", "blood orange", "mandarin", "clementine"]},
    {"sv": ["banan"], "en": ["banana"]},
    {"sv": ["jordgubbar", "jordgubbe"], "en": ["strawberry", "strawberries"]},
    {"sv": ["hallon"], "en": ["raspberry", "raspberries"]},
    {"sv": ["blåbär"], "en": ["blueberry", "blueberries"]},
    {"sv": ["lingon", "lingonsylt"], "en": ["lingonberry", "lingonberries"]},
    {"sv": ["päron"], "en": ["pear"]},
    {"sv": ["mango"], "en": ["mango"]},
    {"sv": ["ananas"], "en": ["pineapple"]},
    {"sv": ["vindruvor", "druvor"], "en": ["grape", "grapes"]},
    {"sv": ["vattenmelon", "melon", "honungsmelon"], "en": ["watermelon", "melon", "honeydew"]},
    {"sv": ["persika", "nektarin"], "en": ["peach", "nectarine"]},
    {"sv": ["plommon"], "en": ["plum"]},
    {"sv": ["fikon"], "en": ["fig", "figs"]},

    # Herbs & Spices
    {"sv": ["basilika"], "en": ["basil"]},
    {"sv": ["persilja"], "en": ["parsley"]},
    {"sv": ["koriander"], "en": ["cilantro", "coriander"]},
    {"sv": ["dill"], "en": ["dill"]},
    {"sv": ["rosmarin"], "en": ["rosemary"]},
    {"sv": ["timjan"], "en": ["thyme"]},
    {"sv": ["oregano"], "en": ["oregano"]},
    {"sv": ["mynta"], "en": ["mint"]},
    {"sv": ["ingefära"], "en": ["ginger"]},
    {"sv": ["kanel"], "en": ["cinnamon"]},
    {"sv": ["peppar", "svartpeppar", "vitpeppar"], "en": ["pepper", "black pepper", "white pepper"]},
    {"sv": ["salt", "havssalt", "flingsalt"], "en": ["salt", "sea salt", "flake salt"]},
    {"sv": ["chili", "chiliflingor"], "en": ["chili", "chilli", "chili flakes"]},
    {"sv": ["saffran"], "en": ["saffron"]},
    {"sv": ["kardemumma"], "en": ["cardamom"]},

    # Pantry / Dry goods
    {"sv": ["mjöl", "vetemjöl", "fullkornsm jöl"], "en": ["flour", "wheat flour", "wholemeal flour"]},
    {"sv": ["socker", "strösocker", "florsocker", "råsocker"], "en": ["sugar", "granulated sugar", "powdered sugar", "raw sugar"]},
    {"sv": ["ris", "jasminris", "basmatiris", "risotto"], "en": ["rice", "jasmine rice", "basmati rice", "risotto"]},
    {"sv": ["pasta", "spaghetti", "penne", "fusilli", "tagliatelle", "linguine"], "en": ["pasta", "spaghetti", "penne", "fusilli", "tagliatelle", "linguine"]},
    {"sv": ["bröd", "surdegsbröd", "knäckebröd", "hamburgerbröd"], "en": ["bread", "sourdough", "crispbread", "burger bun"]},
    {"sv": ["olivolja", "rapsolja", "solrosolja", "sesamolja", "kokosolja"], "en": ["olive oil", "rapeseed oil", "sunflower oil", "sesame oil", "coconut oil"]},
    {"sv": ["vinäger", "balsamvinäger", "vitvinsvinäger", "äppelcidervinäger"], "en": ["vinegar", "balsamic vinegar", "white wine vinegar", "apple cider vinegar"]},
    {"sv": ["sojasås", "soja"], "en": ["soy sauce"]},
    {"sv": ["senap", "dijonsenap", "grovsenap"], "en": ["mustard", "dijon mustard", "whole grain mustard"]},
    {"sv": ["ketchup", "tomatketchup"], "en": ["ketchup"]},
    {"sv": ["majonnäs", "mayo", "aioli"], "en": ["mayonnaise", "mayo", "aioli"]},
    {"sv": ["nötter", "valnötter", "mandel", "cashew", "hasselnötter", "pistagenötter", "jordnötter", "pinjeкärnor"], "en": ["nuts", "walnuts", "almond", "cashew", "hazelnut", "pistachio", "peanuts", "pine nuts"]},

    # Beverages
    {"sv": ["kaffe", "espresso"], "en": ["coffee", "espresso"]},
    {"sv": ["te", "grönt te", "svart te"], "en": ["tea", "green tea", "black tea"]},
    {"sv": ["juice", "apelsinjuice"], "en": ["juice", "orange juice"]},
    {"sv": ["vatten", "kolsyrat vatten", "mineralvatten"], "en": ["water", "sparkling water", "mineral water"]},
    {"sv": ["öl", "lager", "ale", "ipa", "stout"], "en": ["beer", "lager", "ale", "ipa", "stout"]},
    {"sv": ["vin", "rödvin", "vitt vin", "rosévin"], "en": ["wine", "red wine", "white wine", "rosé"]},
]


def search_food_terms(query):
    """
    Given a search query, return a set of search terms in both Swedish and English.
    For example, searching "carrot" returns {"carrot", "carrots", "morot", "morötter"}.
    """
    query_lower = query.lower().strip()
    terms = {query_lower}

    for entry in FOOD_DICTIONARY:
        sv_terms = [t.lower() for t in entry["sv"]]
        en_terms = [t.lower() for t in entry["en"]]
        all_terms = sv_terms + en_terms

        # Check if query matches any term in this entry
        matched = False
        for term in all_terms:
            if query_lower in term or term in query_lower:
                matched = True
                break

        if matched:
            # Add all Swedish and English terms for this food
            for t in sv_terms:
                terms.add(t)
            for t in en_terms:
                terms.add(t)

    return terms
