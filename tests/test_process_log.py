"""O process.log do subprocesso `process` chega ao disco enquanto o filho VIVE (#179).

O pai redireciona o stdout do filho para o process.log da pasta da reunião
(main._process_subprocess). Stdout ligado a ARQUIVO é block-buffered em 8 KB, e
nada no pipeline dava flush: o progresso só era gravado quando o processo morria.
Efeito no suporte: process.log com 86 linhas de traceback nas execuções que
CRASHARAM e zero linha nas que PENDURARAM - justamente as que precisavam ser
explicadas. O tamanho do process.log também é um dos três sinais de vida do
watchdog do pai (main._sinais_do_filho), e só andava de 8 KB em 8 KB.

O teste de ponta a ponta usa processo e arquivo de verdade: com stream falso o
buffer do SO não existe e o bug não aparece.
"""

import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scriba.cli import _StampWriter  # noqa: E402

RAIZ = Path(__file__).resolve().parent.parent

# imprime e PENDURA, sem sair: é o filho travado do #176 em miniatura
FILHO = """
import sys, time
sys.path.insert(0, {raiz!r})
from scriba.cli import _timestamp_subprocess_output
_timestamp_subprocess_output()
print("estagio: transcrevendo")
time.sleep(120)
"""


class _StreamFalso:
    """Stream que só conta o que recebe - inclusive os flushes."""

    def __init__(self):
        self.texto = []
        self.flushes = 0

    def write(self, s):
        self.texto.append(s)
        return len(s)

    def flush(self):
        self.flushes += 1

    def valor(self):
        return "".join(self.texto)


class StampWriterFlushTest(unittest.TestCase):
    def test_flush_a_cada_quebra_de_linha(self):
        s = _StreamFalso()
        w = _StampWriter(s)

        w.write("linha inteira\n")
        self.assertEqual(s.flushes, 1)

        # print() escreve o texto e o "\n" em chamadas separadas: o flush é o do "\n"
        w.write("outra linha")
        self.assertEqual(s.flushes, 1, "linha ainda aberta não precisa ir ao disco")
        w.write("\n")
        self.assertEqual(s.flushes, 2)

    def test_carimbo_de_hora_preservado(self):
        s = _StreamFalso()
        _StampWriter(s).write("oi\n")
        self.assertRegex(s.valor(), r"^\d\d:\d\d:\d\d oi\n$")

    def test_flush_do_stream_nao_derruba_o_filho(self):
        class _Explode(_StreamFalso):
            def flush(self):
                raise OSError("disco cheio")

        # flush que levanta viraria crash no meio da transcrição
        _StampWriter(_Explode()).write("oi\n")


class ProcessLogVivoTest(unittest.TestCase):
    """O caso real: filho de verdade, arquivo de verdade, filho ainda vivo."""

    def test_saida_do_filho_pendurado_chega_ao_disco(self):
        with tempfile.TemporaryDirectory() as tmp:
            pasta = Path(tmp)
            script = pasta / "filho.py"
            script.write_text(FILHO.format(raiz=str(RAIZ)), encoding="utf-8")
            log = pasta / "process.log"

            out = open(log, "a", encoding="utf-8", errors="replace")
            out.write("\n==== scriba process ====\n")
            out.flush()
            banner = log.stat().st_size
            try:
                proc = subprocess.Popen(
                    [sys.executable, str(script)],
                    stdout=out,
                    stderr=subprocess.STDOUT,
                )
            finally:
                out.close()

            try:
                limite = time.monotonic() + 30
                while time.monotonic() < limite:
                    if log.stat().st_size > banner:
                        break
                    if proc.poll() is not None:
                        self.fail(f"o filho morreu antes de escrever (rc={proc.returncode})")
                    time.sleep(0.05)
                else:
                    self.fail("process.log continuou só com o banner: a saída do filho "
                              "ficou presa no buffer (é o bug do #176)")

                self.assertIsNone(proc.poll(), "o filho tem que estar VIVO na hora da leitura")
                self.assertIn("estagio: transcrevendo",
                              log.read_text(encoding="utf-8", errors="replace"))
            finally:
                proc.kill()
                proc.wait(timeout=10)


if __name__ == "__main__":
    unittest.main(verbosity=2)
