"""Idempotent seed: categories, sample products (ship/download/both), variants,
images, and one admin user. Safe to run repeatedly."""

from app import models
from app.auth import hash_password
from app.database import SessionLocal
from app.config import get_settings

settings = get_settings()


def seed() -> None:
    db = SessionLocal()
    try:
        # Admin user (development only).
        admin = (
            db.query(models.User).filter(models.User.email == settings.SEED_ADMIN_EMAIL.lower()).first()
        )
        if not admin:
            admin = models.User(
                email=settings.SEED_ADMIN_EMAIL.lower(),
                password_hash=hash_password(settings.SEED_ADMIN_PASSWORD or "change-me-admin"),
                role="admin",
            )
            db.add(admin)
            db.flush()

        def category(name: str, slug: str) -> models.Category:
            cat = db.query(models.Category).filter(models.Category.slug == slug).first()
            if not cat:
                cat = models.Category(name=name, slug=slug)
                db.add(cat)
                db.flush()
            return cat

        electronics = category("Electronics", "electronics")
        books = category("Books", "books")
        media = category("Media", "media")

        def product(
            *,
            name: str,
            slug: str,
            category: models.Category,
            delivery_type: str,
            description: str,
            variants,
        ) -> models.Product:
            prod = db.query(models.Product).filter(models.Product.slug == slug).first()
            if not prod:
                prod = models.Product(
                    name=name,
                    slug=slug,
                    description=description,
                    delivery_type=delivery_type,
                    category_id=category.id,
                    active=True,
                )
                db.add(prod)
                db.flush()
            # Ensure at least one default variant exists.
            existing = {v.sku for v in prod.variants}
            for sku, options, price, stock in variants:
                if sku not in existing:
                    db.add(
                        models.ProductVariant(
                            product_id=prod.id,
                            sku=sku,
                            options=options,
                            price_minor=price,
                            currency="USD",
                            stock=stock,
                            reserved_stock=0,
                            active=True,
                        )
                    )
            return product

        product(
            name="Wireless Headphones",
            slug="wireless-headphones",
            category=electronics,
            delivery_type="ship",
            description="Over-ear wireless headphones.",
            variants=[
                ("WH-001", {"color": "black"}, 9999, 50),
                ("WH-002", {"color": "white"}, 9999, 40),
            ],
        )
        product(
            name="E-Book: PostgreSQL Guide",
            slug="postgresql-guide",
            category=books,
            delivery_type="download",
            description="A practical guide to PostgreSQL.",
            variants=[
                ("PG-EBOOK-001", {"format": "pdf"}, 1499, 1000),
            ],
        )
        product(
            name="Album: Digital Download",
            slug="album-download",
            category=media,
            delivery_type="both",
            description="Music album with optional physical CD.",
            variants=[
                ("ALB-DIG-001", {"format": "mp3"}, 1299, 1000),
                ("ALB-CD-001", {"format": "cd"}, 1999, 30),
            ],
        )

        db.commit()
    finally:
        db.close()


if __name__ == "__main__":
    seed()
    print("Seed complete.")
