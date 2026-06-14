"""Assistente de perfil (wizard): gera o prompt.md e as hotwords do usuário.

Janela própria, aberta no primeiro uso (sem prompt.md) ou pelo botão da aba
Resumo. O usuário descreve profissão/área/stack/jargão; a geração por IA
(claude -p) escreve um prompt sob medida — validado contra o contrato
estrutural do leitor de notas — com os templates locais como alternativa
offline. Nada é gravado sem o usuário ver o preview e clicar Aplicar.
"""

from __future__ import annotations

import threading
import tkinter as tk
from tkinter import ttk

from . import promptgen, util
from .widgets import (
    FONT,
    FONT_BOLD,
    PALETTE,
    LinkLabel,
    ModernButton,
    enable_dark_titlebar,
    make_entry,
    make_text,
)

_BG = PALETTE["bg"]


class WizardWindow:
    def __init__(self, root: tk.Misc, app=None, on_applied=None):
        self.app = app
        self.on_applied = on_applied  # callback extra (ex.: quit do modo standalone)
        self._titlebar_done = False
        self._result: tuple[str, str] | None = None  # (prompt, hotwords) prontos p/ aplicar
        self._busy = False  # chamada de IA em andamento (botões viram no-op)
        self.win = tk.Toplevel(root)
        self.win.withdraw()
        self.win.title("ScribaDev — Assistente de perfil")
        self.win.configure(bg=_BG)
        self.win.attributes("-alpha", 0.97)
        self.win.minsize(780, 640)
        self.win.protocol("WM_DELETE_WINDOW", self.hide)
        try:
            self.win.iconbitmap(str(util.ICON_ICO))
        except Exception:
            pass

        body = tk.Frame(self.win, bg=_BG, padx=16, pady=12)
        body.pack(fill="both", expand=True)

        tk.Label(
            body,
            text="Atas na língua da sua profissão",
            bg=_BG, fg=PALETTE["text"], font=("Segoe UI", 13, "bold"),
        ).pack(anchor="w")
        tk.Label(
            body,
            text="Descreva seu perfil e o ScribaDev escreve as instruções (prompt.md) que moldam o resumo "
                 "das suas reuniões — e o vocabulário que guia a transcrição. Você revisa antes de aplicar.",
            bg=_BG, fg=PALETTE["muted"], font=FONT, justify="left", wraplength=720,
        ).pack(anchor="w", pady=(2, 10))

        form = tk.Frame(body, bg=_BG)
        form.pack(fill="x")

        row = tk.Frame(form, bg=_BG)
        row.pack(fill="x", pady=3)
        tk.Label(row, text="Perfil base", bg=_BG, fg=PALETTE["text"], font=FONT, width=18, anchor="w").pack(side="left")
        self.base_var = tk.StringVar(value=promptgen.BASE_LABELS["generic"])
        ttk.Combobox(
            row, textvariable=self.base_var, state="readonly", font=FONT, width=34,
            values=list(promptgen.BASE_LABELS.values()),
        ).pack(side="left")

        self.role_var = self._entry_row(form, "Profissão / papel", "ex.: Gerente de projetos de TI")
        self.area_var = self._entry_row(form, "Área / indústria", "ex.: varejo, projetos SAP, logística")
        self.stack_var = self._entry_row(form, "Stack / ferramentas", "ex.: Jira, MS Project, SAP, Excel")

        # difícil saber o próprio jargão de cabeça: a IA sugere a partir do perfil acima
        self.jargon_text, self.jargon_link = self._text_row(
            form, "Jargão e termos frequentes",
            "termos que aparecem nas suas calls (a transcrição aprende com eles)",
            action=("Sugerir com IA", self._suggest_jargon),
        )
        self.musthave_text, _ = self._text_row(form, "O que não pode faltar na ata",
                                               "ex.: ações com responsável e prazo, riscos, decisões")

        btns = tk.Frame(body, bg=_BG)
        btns.pack(fill="x", pady=(10, 4))
        self.ai_btn = ModernButton(btns, "Gerar com IA (recomendado)", self._generate_ai,
                                   kind="primary", width=210)
        self.ai_btn.pack(side="left")
        self.tpl_btn = ModernButton(btns, "Usar modelo pronto", self._generate_template, width=160)
        self.tpl_btn.pack(side="left", padx=(8, 0))
        self.apply_btn = ModernButton(btns, "Aplicar", self._apply, width=110)
        self.apply_btn.pack(side="right")

        self.status_var = tk.StringVar(value="")
        tk.Label(body, textvariable=self.status_var, bg=_BG, fg=PALETTE["muted"],
                 font=("Segoe UI", 9, "italic")).pack(anchor="w", pady=(0, 4))

        tk.Label(body, text="Prévia do prompt (o que vai reger as suas atas)",
                 bg=_BG, fg=PALETTE["text"], font=FONT_BOLD).pack(anchor="w")
        prev = tk.Frame(body, bg=_BG)
        prev.pack(fill="both", expand=True, pady=(4, 0))
        self.preview = make_text(prev)
        sb = ttk.Scrollbar(prev, orient="vertical", command=self.preview.yview)
        self.preview.configure(yscrollcommand=sb.set, state="disabled")
        sb.pack(side="right", fill="y")
        self.preview.pack(side="left", fill="both", expand=True)
        self.hot_var = tk.StringVar(value="")
        tk.Label(body, textvariable=self.hot_var, bg=_BG, fg=PALETTE["muted"],
                 font=("Segoe UI", 8), wraplength=740, justify="left").pack(anchor="w", pady=(4, 0))

    # ------------------------------------------------------------- blocos ----

    def _entry_row(self, parent, label: str, hint: str) -> tk.StringVar:
        row = tk.Frame(parent, bg=_BG)
        row.pack(fill="x", pady=3)
        tk.Label(row, text=label, bg=_BG, fg=PALETTE["text"], font=FONT, width=18, anchor="w").pack(side="left")
        var = tk.StringVar()
        make_entry(row, var).pack(side="left", fill="x", expand=True, ipady=3)
        tk.Label(parent, text=hint, bg=_BG, fg=PALETTE["muted"], font=("Segoe UI", 8)).pack(
            anchor="w", padx=(0, 0), pady=(0, 2)
        )
        return var

    def _text_row(self, parent, label: str, hint: str,
                  action: tuple[str, object] | None = None) -> tuple[tk.Text, LinkLabel | None]:
        head = tk.Frame(parent, bg=_BG)
        head.pack(fill="x", pady=(4, 1))
        tk.Label(head, text=label, bg=_BG, fg=PALETTE["text"], font=FONT, anchor="w").pack(side="left")
        link = None
        if action:
            link = LinkLabel(head, action[0], action[1])
            link.pack(side="right")
        txt = make_text(parent)
        txt.configure(height=2)
        txt.pack(fill="x")
        tk.Label(parent, text=hint, bg=_BG, fg=PALETTE["muted"], font=("Segoe UI", 8)).pack(
            anchor="w", pady=(0, 2)
        )
        return txt, link

    # ----------------------------------------------------------- geração ----

    def _profile(self) -> promptgen.Profile:
        label_to_key = {v: k for k, v in promptgen.BASE_LABELS.items()}
        return promptgen.Profile(
            base=label_to_key.get(self.base_var.get(), "generic"),
            role=self.role_var.get().strip(),
            area=self.area_var.get().strip(),
            stack=self.stack_var.get().strip(),
            jargon=self.jargon_text.get("1.0", "end").strip(),
            must_have=self.musthave_text.get("1.0", "end").strip(),
        )

    def _spawn(self, work, on_done) -> None:
        """Roda `work` numa thread e entrega o resultado a `on_done` na thread do Tk."""
        box: dict = {}

        def runner() -> None:
            try:
                box["result"] = work()
            except Exception:
                box["result"] = None

        threading.Thread(target=runner, daemon=True, name="wizard-ai").start()

        def poll() -> None:
            if "result" not in box:
                self.win.after(300, poll)
                return
            on_done(box["result"])

        poll()

    def _generate_template(self) -> None:
        if self._busy:
            return
        prompt, hotwords = promptgen.template_prompt(self._profile())
        self._show_result(prompt, hotwords, "modelo pronto")

    def _generate_ai(self) -> None:
        if self._busy:
            return
        profile = self._profile()
        self._busy = True
        self.ai_btn.set_text("Gerando…")
        self.status_var.set("Gerando com IA… isso leva por volta de um minuto.")

        def done(outcome) -> None:
            self._busy = False
            self.ai_btn.set_text("Gerar com IA (recomendado)")
            if outcome:
                prompt, hotwords = outcome
                self._show_result(prompt, hotwords, "gerado por IA e validado")
            else:
                self.status_var.set(
                    "Não consegui gerar com IA agora (claude indisponível ou resposta fora do "
                    "padrão). O botão \"Usar modelo pronto\" funciona offline."
                )

        self._spawn(lambda: promptgen.ai_prompt(profile), done)

    def _suggest_jargon(self) -> None:
        """Preenche o campo de jargão com sugestões da IA (mescla, nunca apaga o digitado)."""
        if self._busy:
            return
        profile = self._profile()
        self._busy = True
        self.jargon_link.configure(text="Sugerindo…")
        self.status_var.set("Buscando o jargão típico do seu perfil…")

        def done(suggested) -> None:
            self._busy = False
            self.jargon_link.configure(text="Sugerir com IA")
            if not suggested:
                self.status_var.set(
                    "Não consegui sugerir agora (claude indisponível). Digite alguns termos "
                    "que você ouve nas suas reuniões — ou siga sem: o campo é opcional."
                )
                return
            current = [t.strip() for t in self.jargon_text.get("1.0", "end").split(",") if t.strip()]
            merged = list(current)
            for term in (t.strip() for t in suggested.split(",")):
                if term and term.lower() not in (m.lower() for m in merged):
                    merged.append(term)
            self.jargon_text.delete("1.0", "end")
            self.jargon_text.insert("1.0", ", ".join(merged))
            self.status_var.set(
                f"{len(merged) - len(current)} termo(s) sugerido(s) — corte o que não fizer "
                "sentido e ajuste à vontade."
            )

        self._spawn(lambda: promptgen.suggest_jargon(profile), done)

    def _show_result(self, prompt: str, hotwords: str, origin: str) -> None:
        self._result = (prompt, hotwords)
        self.preview.configure(state="normal")
        self.preview.delete("1.0", "end")
        self.preview.insert("1.0", prompt)
        self.preview.configure(state="disabled")
        self.hot_var.set(f"Vocabulário para a transcrição (hotwords): {hotwords or '(mantém o atual)'}")
        self.status_var.set(f"Prévia pronta ({origin}). Revise e clique em Aplicar.")

    def _apply(self) -> None:
        if self._busy:
            return
        if not self._result:
            self.status_var.set("Gere uma prévia primeiro (IA ou modelo pronto).")
            return
        prompt, hotwords = self._result
        backup = promptgen.apply_prompt(prompt, hotwords or None)
        promptgen.mark_wizard_done()
        if self.app is not None:
            try:
                self.app.reload_config()
            except Exception:
                pass
        extra = f" O prompt anterior ficou em {backup.name}." if backup else ""
        self.status_var.set(f"Aplicado ✓ — as próximas atas usam este perfil.{extra}")
        if self.on_applied:
            self.win.after(1200, self.on_applied)

    # ------------------------------------------------------------- janela ----

    def show(self) -> None:
        self.win.deiconify()
        if not self._titlebar_done:
            self._titlebar_done = True
            enable_dark_titlebar(self.win)
        self.win.lift()
        self.win.focus_force()

    def hide(self) -> None:
        self.win.withdraw()
