/* M4-only native main executable. Link the unchanged shipped Go archive here,
 * as Swift does, before starting the existing Python private-pipe worker.
 * The interpreter library is the running driver's existing Python installation.
 */
#include <dlfcn.h>
#include <stdio.h>
#include "tsnet_shim.h"

int main(int argc, char **argv) {
    /* Go's archive constructor starts runtime initialization on another thread.
     * An exported call waits for package initialization, including Darwin's
     * startup argv/env/apple capture, before Python can modify that vector.
     * No tsnet.Server is constructed or started by this query.
     */
    char *error = tsnet_last_error();
    tsnet_free_string(error);
    void *python = dlopen(M4_PYTHON_LIBRARY, RTLD_NOW | RTLD_GLOBAL);
    if (python == NULL) {
        fputs("M4 native host interpreter unavailable\n", stderr);
        return 1;
    }
    int (*python_main)(int, char **) = (int (*)(int, char **))dlsym(python, "Py_BytesMain");
    if (python_main == NULL) {
        fputs("M4 native host interpreter entry unavailable\n", stderr);
        return 1;
    }
    return python_main(argc, argv);
}
