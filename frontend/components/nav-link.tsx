"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

/** Sidebar entry. Marks itself active from the current path so the shell can
 *  stay a server component. */
export function NavLink({ href, label }: { href: string; label: string }) {
  const pathname = usePathname();
  const active = pathname === href || (href !== "/" && pathname.startsWith(href));
  return (
    <Link href={href} className={`nav-item${active ? " active" : ""}`}>
      <span className="nav-dot" aria-hidden />
      {label}
    </Link>
  );
}
