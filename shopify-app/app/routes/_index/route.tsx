import type { LoaderFunctionArgs } from "react-router";
import { redirect, Form, useLoaderData } from "react-router";

import { login } from "../../shopify.server";

import styles from "./styles.module.css";

export const loader = async ({ request }: LoaderFunctionArgs) => {
  const url = new URL(request.url);

  if (url.searchParams.get("shop")) {
    throw redirect(`/app?${url.searchParams.toString()}`);
  }

  return { showForm: Boolean(login) };
};

export default function App() {
  const { showForm } = useLoaderData<typeof loader>();

  return (
    <div className={styles.index}>
      <div className={styles.content}>
        <h1 className={styles.heading}>Agent Trust</h1>
        <p className={styles.text}>
          See which AI agents browse and buy from your store, and keep the evidence when an agent order is disputed.
        </p>
        {showForm && (
          <Form className={styles.form} method="post" action="/auth/login">
            <label className={styles.label}>
              <span>Shop domain</span>
              <input className={styles.input} type="text" name="shop" />
              <span>e.g: my-shop-domain.myshopify.com</span>
            </label>
            <button className={styles.button} type="submit">
              Log in
            </button>
          </Form>
        )}
        <ul className={styles.list}>
          <li>
            <strong>See every agent</strong>. AI assistants, crawlers, and agents
            driving a browser without saying so.
          </li>
          <li>
            <strong>Know who placed each order</strong>. Agent orders flagged with
            the evidence behind them, including carts built through Shopify's agent API.
          </li>
          <li>
            <strong>Evidence for disputes</strong>. A record of what the agent did,
            ready when a chargeback arrives.
          </li>
        </ul>
      </div>
    </div>
  );
}
