import { redirect } from "next/navigation";

export default async function InvoicesPage({
  searchParams,
}: {
  searchParams: Promise<{ search?: string; q?: string }>;
}) {
  const params = await searchParams;
  const q = params.q || params.search;
  if (q) {
    redirect(`/queue?q=${encodeURIComponent(q)}`);
  }
  redirect("/queue");
}
