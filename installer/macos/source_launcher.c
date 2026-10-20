/* Lançador do ScribaDev em modo código-fonte: HOSPEDA o Python do venv dentro deste
   executável (embedding), em vez de chamar o binário do Python. Assim o processo é o
   .app do começo ao fim e o macOS atribui TODAS as permissões (microfone, áudio do
   sistema, Acessibilidade) a ele - chamando o python3, o processo vira Python.app e a
   Acessibilidade ia para o Python. */
#include <Python.h>
#include <limits.h>
#include <mach-o/dyld.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#ifndef VENV_PYTHON
#error "compile com -DVENV_PYTHON=\"<venv>/bin/python3\""
#endif
/* --launcher-check: diagnóstico (make_source_app.py/testes) - quem hospeda o Python,
   de onde vem o scriba e se a Acessibilidade está concedida a ESTE processo. */
#define RUN_COMMAND \
    "import sys\n" \
    "if sys.argv[1:2] == ['--launcher-check']:\n" \
    "    import json, os, scriba\n" \
    "    try:\n" \
    "        import ApplicationServices as AS; ax = bool(AS.AXIsProcessTrusted())\n" \
    "    except Exception: ax = None\n" \
    "    print(json.dumps({'prefix': sys.prefix, 'scriba': scriba.__file__, 'accessibility': ax,\n" \
    "                      'bundle': os.environ.get('SCRIBADEV_APP_BUNDLE')}))\n" \
    "    sys.exit(0)\n" \
    "from scriba.cli import main_tray\n" \
    "sys.exit(main_tray())\n"

static void bundle_dir(char *out, size_t size) {
    char raw[PATH_MAX], real[PATH_MAX];
    uint32_t n = sizeof raw;
    out[0] = '\0';
    if (_NSGetExecutablePath(raw, &n) != 0 || !realpath(raw, real)) return;
    for (int i = 0; i < 3; i++) {          /* X.app/Contents/MacOS/exe -> X.app */
        char *slash = strrchr(real, '/');
        if (!slash) return;
        *slash = '\0';
    }
    snprintf(out, size, "%s", real);
}

static int fail(PyConfig *config, PyStatus status) {
    PyConfig_Clear(config);
    if (PyStatus_IsExit(status)) return status.exitcode;
    Py_ExitStatusException(status);
    return 1;
}

int main(int argc, char *argv[]) {
    char bundle[PATH_MAX];
    bundle_dir(bundle, sizeof bundle);
    if (bundle[0]) setenv("SCRIBADEV_APP_BUNDLE", bundle, 1);   /* autostart abre ESTE .app */

    PyConfig config;
    PyConfig_InitPythonConfig(&config);
    config.parse_argv = 0;                 /* argv vai inteiro p/ o app (--minimized) */
    PyStatus st = PyConfig_SetBytesString(&config, &config.executable, VENV_PYTHON);
    if (PyStatus_Exception(st)) return fail(&config, st);
    st = PyConfig_SetBytesString(&config, &config.run_command, RUN_COMMAND);
    if (PyStatus_Exception(st)) return fail(&config, st);
    st = PyConfig_SetBytesArgv(&config, argc, argv);
    if (PyStatus_Exception(st)) return fail(&config, st);
    st = Py_InitializeFromConfig(&config);
    if (PyStatus_Exception(st)) return fail(&config, st);
    PyConfig_Clear(&config);
    return Py_RunMain();
}
