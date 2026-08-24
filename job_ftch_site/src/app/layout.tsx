import type { Metadata } from "next";
import "./globals.css";
import { ProjectionDock } from "@/components/molecules/projection-dock";
import { ConsentedAnalytics } from "@/components/organisms/consented-analytics";
import { CookieConsent } from "@/components/organisms/cookie-consent";
import { SiteShell } from "@/components/layouts/site-shell";

export const metadata: Metadata = {
  title: "job_ftch — open-source pipeline для любых вакансий",
  description: "Library-first pipeline для сбора, нормализации и публикации вакансий из Telegram, карьерных сайтов, RSS и API.",
  icons: { icon: "/brand/job-ftch-icon.png", apple: "/brand/job-ftch-icon.png" },
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="ru" data-theme="dark"><body><SiteShell>{children}</SiteShell><ProjectionDock /><CookieConsent /><ConsentedAnalytics /></body></html>;
}
