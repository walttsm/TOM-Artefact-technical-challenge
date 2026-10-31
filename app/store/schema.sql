DROP TABLE IF EXISTS order_items;
DROP TABLE IF EXISTS orders;
DROP TABLE IF EXISTS customers;
DROP TABLE IF EXISTS promotions;
DROP TABLE IF EXISTS products;
DROP TABLE IF EXISTS categories;

CREATE TABLE categories (
    category_id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    description TEXT NOT NULL,
    name_search TEXT NOT NULL,
    description_search TEXT NOT NULL
) STRICT;

CREATE TABLE products (
    product_id INTEGER PRIMARY KEY,
    category_id INTEGER NOT NULL REFERENCES categories (category_id),
    name TEXT NOT NULL,
    description TEXT NOT NULL,
    price_brl REAL NOT NULL CHECK (price_brl >= 0),
    stock_quantity INTEGER NOT NULL CHECK (stock_quantity >= 0),
    status TEXT NOT NULL CHECK (status IN ('active', 'discontinued', 'coming_soon')),
    specs TEXT NOT NULL CHECK (json_valid(specs)),
    created_at TEXT NOT NULL CHECK (date(created_at) = created_at),
    name_search TEXT NOT NULL,
    description_search TEXT NOT NULL
) STRICT;

-- A product keeps its past promotions, so product_id repeats.
CREATE TABLE promotions (
    promotion_id INTEGER PRIMARY KEY,
    product_id INTEGER NOT NULL REFERENCES products (product_id),
    discount_percent INTEGER NOT NULL CHECK (discount_percent BETWEEN 1 AND 100),
    description TEXT NOT NULL,
    is_active INTEGER NOT NULL CHECK (is_active IN (0, 1)),
    description_search TEXT NOT NULL
) STRICT;

CREATE UNIQUE INDEX one_active_promotion_per_product
    ON promotions (product_id) WHERE is_active = 1;

-- Email is not unique: two customers in the source data share one.
-- email_is_shared marks those rows, and phone is what tells them apart.
CREATE TABLE customers (
    customer_id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    phone TEXT NOT NULL UNIQUE CHECK (phone <> '' AND phone NOT GLOB '*[^0-9]*'),
    email TEXT NOT NULL CHECK (email = lower(email)),
    city TEXT NOT NULL,
    email_is_shared INTEGER NOT NULL CHECK (email_is_shared IN (0, 1))
) STRICT;

CREATE TABLE orders (
    order_id INTEGER PRIMARY KEY,
    customer_id INTEGER NOT NULL REFERENCES customers (customer_id),
    order_date TEXT NOT NULL CHECK (date(order_date) = order_date),
    status TEXT NOT NULL
        CHECK (status IN ('pending', 'confirmed', 'shipped', 'delivered', 'cancelled')),
    total_brl REAL NOT NULL CHECK (total_brl >= 0),
    payment_method TEXT NOT NULL
        CHECK (payment_method IN ('pix', 'boleto', 'debit') OR payment_method GLOB 'credit_*x'),
    tracking_code TEXT,
    estimated_delivery TEXT CHECK (date(estimated_delivery) = estimated_delivery),
    notes TEXT
) STRICT;

CREATE TABLE order_items (
    order_id INTEGER NOT NULL REFERENCES orders (order_id),
    product_id INTEGER NOT NULL REFERENCES products (product_id),
    quantity INTEGER NOT NULL CHECK (quantity > 0),
    PRIMARY KEY (order_id, product_id)
) STRICT;

-- Not in the DROP list above: handoffs are created at runtime and must survive re-ingestion.
CREATE TABLE IF NOT EXISTS handoffs (
    handoff_id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL CHECK (kind IN ('reclamacao', 'atendimento_humano')),
    summary TEXT NOT NULL CHECK (summary <> ''),
    contact TEXT NOT NULL CHECK (contact <> ''),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
) STRICT;
