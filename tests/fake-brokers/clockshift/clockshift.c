/* LD_PRELOAD shim for live runs: the wall clock (CLOCK_REALTIME, gettimeofday, time) reads
   JB_CLOCK_SHIFT seconds ahead; monotonic clocks and every kind of sleep are left alone (unlike
   libfaketime, whose clock_nanosleep handling breaks Python's time.sleep).
   build: gcc -shared -fPIC -O2 -o clockshift.so clockshift.c -ldl */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <stdlib.h>
#include <sys/time.h>
#include <time.h>

static long long shift = 0;
static int ready = 0;
static int (*real_clock_gettime)(clockid_t, struct timespec *);
static int (*real_gettimeofday)(struct timeval *, void *);

static void init(void) {
    if (ready) return;
    const char *s = getenv("JB_CLOCK_SHIFT");
    shift = s ? atoll(s) : 0;
    real_clock_gettime = dlsym(RTLD_NEXT, "clock_gettime");
    real_gettimeofday = dlsym(RTLD_NEXT, "gettimeofday");
    ready = 1;
}

int clock_gettime(clockid_t id, struct timespec *tp) {
    init();
    int r = real_clock_gettime(id, tp);
    if (r == 0 && (id == CLOCK_REALTIME || id == CLOCK_REALTIME_COARSE)) tp->tv_sec += shift;
    return r;
}

int gettimeofday(struct timeval *tv, void *tz) {
    init();
    int r = real_gettimeofday(tv, tz);
    if (r == 0 && tv) tv->tv_sec += shift;
    return r;
}

time_t time(time_t *t) {
    struct timespec ts;
    clock_gettime(CLOCK_REALTIME, &ts);
    if (t) *t = ts.tv_sec;
    return ts.tv_sec;
}
