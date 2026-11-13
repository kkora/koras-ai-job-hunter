import { WorkspaceApp } from "@/components/workspace-app";
import { Suspense } from "react";

export const dynamic = "force-dynamic";

export default function WorkspacePage() {
  return <Suspense fallback={<div className="boot-screen">Opening private workspace…</div>}><WorkspaceApp /></Suspense>;
}
