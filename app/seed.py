import os
from .models import *
from .security import hash_pw
CAT = {
"Land preparation":"Ploughing,Harrowing,Ridging and furrowing,Land clearing,Land surveying",
"Planting & harvesting":"Planting,Weeding,Harvesting,Threshing and shelling,Post-harvest handling",
"Spraying & pest control":"Crop spraying,Pest and disease control,Weed control,Drone spraying",
"Irrigation & water":"Irrigation installation,Irrigation repair,Borehole drilling,Water pumps,Dams and water pans",
"Machinery & equipment hire":"Tractor hire,Combine harvester hire,Planter and sprayer hire,Farm trailers and pickups,Equipment listings",
"Livestock management":"Veterinary services,Dairy management,Poultry management,Animal feeding,Breeding and AI,Livestock trading",
"Farm labour":"Casual farm workers,Farm managers,Milkers,Harvest teams,Skilled farm technicians",
"Agricultural consultancy":"Agronomy advice,Soil testing,Farm planning,Organic and certification,Agri-business advice",
"Greenhouse & horticulture":"Greenhouse construction,Horticulture,Nurseries and seedlings,Flowers and fruits",
"Inputs, feeds & agrovets":"Seeds,Fertilizers,Agrochemicals,Animal feeds,Farm tools",
"Transport, storage & markets":"Produce transport,Cold storage,Warehousing,Produce buyers and off-takers,Agricultural companies"}
SERVICES = [("Ploughing","Land ploughing","Priced per acre."),("Harrowing","Land harrowing","Priced per acre."),
("Planting","Planting service","Priced per acre or per day."),("Harvesting","Harvesting service","Priced per acre or per bag."),
("Crop spraying","Crop spraying","Priced per acre or per tank."),("Irrigation installation","Irrigation installation","Quotation after a site visit."),
("Tractor hire","Tractor hire","Priced per hour or per day, with or without operator."),("Veterinary services","Veterinary farm visit","Quotation per visit."),
("Casual farm workers","Farm labour","Priced per worker per day."),("Agronomy advice","Agronomy consultation","Priced per visit."),
("Soil testing","Soil testing","Quotation per sample."),("Produce transport","Produce transport","Quotation by distance and load.")]
def seed(db):
    if not db.query(User).filter_by(role="super").first():
        db.add(User(email=os.getenv("ADMIN_EMAIL","admin@fambeermconn.local"), name="Super Admin", role="super",
                    password_hash=hash_pw(os.getenv("ADMIN_PASSWORD","ChangeMe!2026"))))
    if not db.query(Category).first():
        for ind, subs in CAT.items():
            p = Category(name=ind); db.add(p); db.flush()
            for s in subs.split(","): db.add(Category(name=s.strip(), parent_id=p.id))
        db.flush()
    if not db.query(Service).first():
        for sub, name, desc in SERVICES:
            c = db.query(Category).filter(Category.name == sub, Category.parent_id != None).first()
            if c: db.add(Service(category_id=c.id, name=name, description=desc, price=0, price_type="quotation", booking_method="request"))
    for k, v in {"countries":"KE","currencies":"KES,USD","languages":"en,sw","tax_pct":"16","whatsapp_number":"","facebook_page":"",
        "escrow_instructions":"Pay the agreed amount by M-Pesa to Paybill/Till 000000 (set this in Admin > Settings). Use your booking ref (e.g. FC-12) as the account, then enter the M-Pesa confirmation code below. Funds are held until the job is done."}.items():
        if not db.query(Setting).filter_by(key=k).first(): db.add(Setting(key=k, value=v))
    db.commit()
