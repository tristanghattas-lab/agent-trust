// Prints the database URL for Prisma, pointed at its own Postgres schema
// ("shopify_app") so it never touches the Agent Trust API's tables in the
// same database. SHOPIFY_DATABASE_URL, if set, wins.
const explicit = process.env.SHOPIFY_DATABASE_URL;
if (explicit) {
  process.stdout.write(explicit);
} else {
  const base = process.env.DATABASE_URL || "";
  if (!base) throw new Error("DATABASE_URL is not set");
  const url = new URL(base);
  url.searchParams.set("schema", "shopify_app");
  process.stdout.write(url.toString());
}
