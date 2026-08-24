"use client";

import { Moon, Sun } from "lucide-react";
import { useEffect, useState } from "react";

export function ThemeToggle() {
  const [dark, setDark] = useState(false);
  useEffect(() => {
    const saved = localStorage.getItem("job_ftch_theme");
    const next = saved !== "light";
    document.documentElement.dataset.theme = next ? "dark" : "light";
    setDark(next);
  }, []);
  function toggle() {
    const next = !dark;
    document.documentElement.dataset.theme = next ? "dark" : "light";
    localStorage.setItem("job_ftch_theme", next ? "dark" : "light");
    setDark(next);
  }
  return <button className="theme-toggle" onClick={toggle} aria-label={dark ? "Светлая тема" : "Тёмная тема"}>{dark ? <Sun size={14} /> : <Moon size={14} />}</button>;
}
