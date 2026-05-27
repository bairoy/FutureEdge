/**
 * frontend/src/app/page.tsx
 * Root redirect — sends users to dashboard or login based on cookie.
 */
import { redirect } from "next/navigation";
import { cookies } from "next/headers";

export default function RootPage() {
  const hasCookie = cookies().has("fe_refresh");
  redirect(hasCookie ? "/dashboard" : "/login");
}