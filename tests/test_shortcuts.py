"""Reparo do ícone dos atalhos (#192): instalação por código-fonte com o repo
movido deixa o IconLocation dos .lnk apontando para um scriba.ico inexistente e a
janela cai no ícone genérico da barra. A decisão é pura (icon_is_stale); leitura e
escrita do .lnk (PowerShell) são injetadas e mockadas aqui.

Roda sem dependências externas:  python -m unittest discover -s tests
"""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scriba import shortcuts  # noqa: E402


class IconIsStaleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="scriba_lnk_"))
        self.tray = self.tmp / "venv" / "Scripts" / "scribadev-tray.exe"
        self.ico_ok = self.tmp / "repo" / "scriba.ico"
        self.ico_ok.parent.mkdir(parents=True)
        self.ico_ok.write_bytes(b"ico")

    def test_alvo_nosso_com_icone_sumido_e_stale(self):
        sumido = str(self.tmp / "OneDrive" / "SandBox" / "scriba.ico") + ",0"
        self.assertTrue(shortcuts.icon_is_stale(str(self.tray), sumido, self.tray))

    def test_icone_vazio_tambem_conta(self):
        self.assertTrue(shortcuts.icon_is_stale(str(self.tray), "", self.tray))
        self.assertTrue(shortcuts.icon_is_stale(str(self.tray), ",0", self.tray))

    def test_icone_valido_nao_e_tocado(self):
        self.assertFalse(shortcuts.icon_is_stale(str(self.tray), f"{self.ico_ok},0", self.tray))

    def test_alvo_de_outra_instalacao_nao_e_tocado(self):
        outro = self.tmp / "outra" / "Scripts" / "scribadev-tray.exe"
        self.assertFalse(shortcuts.icon_is_stale(str(outro), "C:/nao/existe.ico,0", self.tray))
        self.assertFalse(shortcuts.icon_is_stale("", "C:/nao/existe.ico,0", self.tray))

    @unittest.skipUnless(sys.platform == "win32", "caixa/separador só se igualam no Windows (normcase)")
    def test_comparacao_do_alvo_ignora_caixa_e_separador(self):
        # o WScript.Shell devolve o caminho como foi gravado; normcase/normpath igualam
        alvo = str(self.tray).upper().replace("\\", "/")
        self.assertTrue(shortcuts.icon_is_stale(alvo, "C:/nao/existe.ico,0", self.tray))


@unittest.skipUnless(sys.platform == "win32", "atalhos .lnk são do Windows")
class RepairTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="scriba_lnk_"))
        self.tray = self.tmp / "venv" / "Scripts" / "scribadev-tray.exe"
        self.ico = self.tmp / "repo" / "scriba.ico"
        self.ico.parent.mkdir(parents=True)
        self.ico.write_bytes(b"ico")
        self.quebrado = self.tmp / "Start Menu" / "ScribaDev.lnk"
        self.bom = self.tmp / "Desktop" / "ScribaDev.lnk"
        self.alheio = self.tmp / "TaskBar" / "ScribaDev.lnk"
        self.info = {
            self.quebrado: (str(self.tray), "C:/sumiu/scriba.ico,0"),
            self.bom: (str(self.tray), f"{self.ico},0"),
            self.alheio: (str(self.tmp / "outra" / "scribadev-tray.exe"), "C:/sumiu/scriba.ico,0"),
        }
        self.lnks = list(self.info)

    def test_stale_lnks_lista_so_os_quebrados_desta_instalacao(self):
        stale = shortcuts.stale_lnks(self.lnks, self.tray, read=lambda l: self.info)
        self.assertEqual(stale, [self.quebrado])

    def test_repair_regrava_so_os_quebrados_e_devolve_os_consertados(self):
        escritos = []

        def write(lnk, icon):
            escritos.append((lnk, icon))
            return True

        with mock.patch.object(shortcuts.subprocess, "run") as run:   # ie4uinit
            fixed = shortcuts.repair_stale_icons(self.lnks, self.tray, self.ico,
                                                 read=lambda l: self.info, write=write)
        self.assertEqual(fixed, [self.quebrado])
        self.assertEqual(escritos, [(self.quebrado, self.ico)])
        self.assertTrue(run.called)   # cache de ícones do shell é atualizado

    def test_repair_sem_nada_quebrado_nao_mexe_nem_no_cache(self):
        info = {self.bom: self.info[self.bom]}
        with mock.patch.object(shortcuts.subprocess, "run") as run:
            fixed = shortcuts.repair_stale_icons([self.bom], self.tray, self.ico,
                                                 read=lambda l: info,
                                                 write=lambda l, i: self.fail("não devia escrever"))
        self.assertEqual(fixed, [])
        self.assertFalse(run.called)

    def test_repair_sem_ico_atual_nao_faz_nada(self):
        fixed = shortcuts.repair_stale_icons(self.lnks, self.tray, self.tmp / "nao-existe.ico",
                                             read=lambda l: self.info,
                                             write=lambda l, i: self.fail("não devia escrever"))
        self.assertEqual(fixed, [])

    def test_repair_nunca_levanta(self):
        def read(l):
            raise RuntimeError("PowerShell explodiu")

        self.assertEqual(shortcuts.repair_stale_icons(self.lnks, self.tray, self.ico, read=read), [])

    def test_escrita_que_falha_nao_entra_nos_consertados(self):
        with mock.patch.object(shortcuts.subprocess, "run"):
            fixed = shortcuts.repair_stale_icons(self.lnks, self.tray, self.ico,
                                                 read=lambda l: self.info, write=lambda l, i: False)
        self.assertEqual(fixed, [])


if __name__ == "__main__":
    unittest.main()
