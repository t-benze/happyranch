/* Fixed read-only census. Sole input: original workload UID, never a path or
 * command. No product code, environment/argv-of-process/memory reads, writes
 * (except bounded JSON stdout), exec, shell, signals, or host configuration.
 * Native SDK types are used directly; compile only on the admitted runner. */
#define _POSIX_C_SOURCE 200809L
#include <errno.h>
#include <limits.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
#ifdef __linux__
#include <dirent.h>
#include <fcntl.h>
#elif defined(__APPLE__)
#include <libproc.h>
#include <sys/proc_info.h>
#else
#error Unsupported census platform
#endif

#define MAX_ROWS 8192
#define PATH_CAP 4096
#define OUTPUT_CAP (32u * 1024u * 1024u)
struct row {
    int pid, ppid, pgid;
    unsigned uid, ruid, svuid, fsuid;
    char start[64], state[16], cwd[PATH_CAP], exe[PATH_CAP];
};
static struct row rows[MAX_ROWS];
static size_t row_count, output_size;
static unsigned workload_uid;
static int error_pid, error_number, raced, reused;
static const char *error_operation;
static struct timespec begin;
static char output[OUTPUT_CAP];

static int fail(int pid, const char *operation, int number) {
    error_pid = pid; error_operation = operation; error_number = number;
    return -1;
}
static int deadline(void) {
    struct timespec now;
    if (clock_gettime(CLOCK_MONOTONIC, &now)) return fail(0,"clock",errno);
    if (now.tv_sec - begin.tv_sec >= 10) return fail(0,"deadline",ETIMEDOUT);
    return 0;
}
static int owned(const struct row *r) {
    return r->uid == workload_uid || r->ruid == workload_uid ||
           r->svuid == workload_uid || r->fsuid == workload_uid;
}
static int same(const struct row *a, const struct row *b) {
    return a->pid == b->pid && a->ppid == b->ppid && a->pgid == b->pgid &&
           !strcmp(a->start,b->start) && a->uid == b->uid &&
           a->ruid == b->ruid && a->svuid == b->svuid && a->fsuid == b->fsuid;
}
#ifdef __linux__
static int read_fixed(int pid, const char *member, char *buf, size_t size) {
    char path[80];
    int n = snprintf(path,sizeof(path),"/proc/%d/%s",pid,member);
    if (n < 0 || (size_t)n >= sizeof(path)) { errno = EOVERFLOW; return -1; }
    int fd = open(path,O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
    if (fd < 0) return -1;
    size_t used = 0;
    while (used < size - 1) {
        ssize_t got = read(fd,buf+used,size-1-used);
        if (got < 0) { int e=errno; close(fd); errno=e; return -1; }
        if (!got) { buf[used]=0; close(fd); return 0; }
        used += (size_t)got;
    }
    close(fd); errno=EOVERFLOW; return -1;
}
static int identity(int pid, struct row *r) {
    char buf[16384];
    memset(r,0,sizeof(*r)); r->pid=pid;
    if (read_fixed(pid,"stat",buf,sizeof(buf))) return -1;
    char *tail = strrchr(buf,')');
    if (!tail) { errno=EPROTO; return -1; }
    char *cursor=NULL, *part=strtok_r(tail+1," \n",&cursor);
    for (int field=3; field<=22; field++,part=strtok_r(NULL," \n",&cursor)) {
        if (!part) { errno=EPROTO; return -1; }
        if (field==3) snprintf(r->state,sizeof(r->state),"%s",part);
        if (field==4) r->ppid=atoi(part);
        if (field==5) r->pgid=atoi(part);
        if (field==22) snprintf(r->start,sizeof(r->start),"%s",part);
    }
    if (read_fixed(pid,"status",buf,sizeof(buf))) return -1;
    char *uids=strstr(buf,"\nUid:\t");
    if (!uids || sscanf(uids,"\nUid:\t%u %u %u %u",&r->ruid,&r->uid,&r->svuid,&r->fsuid)!=4) {
        errno=EPROTO; return -1;
    }
    return 0;
}
static int absent(int pid) {
    /* ENOENT is an exit only after a second native identity read agrees. */
    struct row again;
    return identity(pid,&again) < 0 && errno==ENOENT;
}
static int paths(int pid, struct row *r, const char **operation) {
    const char *members[]={"cwd","exe"}; char *targets[]={r->cwd,r->exe};
    for (int i=0;i<2;i++) {
        char path[80]; snprintf(path,sizeof(path),"/proc/%d/%s",pid,members[i]);
        *operation = i ? "readlink-exe" : "readlink-cwd";
        ssize_t n=readlink(path,targets[i],PATH_CAP-1);
        if (n<0) return -1;
        if (n>=PATH_CAP-1) { errno=EOVERFLOW; return -1; }
        targets[i][n]=0;
    }
    return 0;
}
static int enumerate(int *pids, size_t *count) {
    DIR *d=opendir("/proc"); if (!d) return fail(0,"opendir-proc",errno);
    struct dirent *entry; errno=0;
    while ((entry=readdir(d))) {
        char *end=NULL; long pid=strtol(entry->d_name,&end,10);
        if (!*entry->d_name || *end || pid<=0 || pid>INT_MAX) continue;
        if (*count==MAX_ROWS) { closedir(d); return fail(0,"pid-cap",EOVERFLOW); }
        pids[(*count)++]=(int)pid;
        errno=0;
    }
    int e=errno; closedir(d); return e ? fail(0,"readdir-proc",e) : 0;
}
#else
static int enumerate(int *pids, size_t *count) {
    int n=proc_listpids(PROC_ALL_PIDS,0,pids,MAX_ROWS*(int)sizeof(int));
    if (n<=0 || n>=MAX_ROWS*(int)sizeof(int) || n%(int)sizeof(int))
        return fail(0,"proc_listpids",n>=MAX_ROWS*(int)sizeof(int)?EOVERFLOW:errno);
    *count=(size_t)n/sizeof(int); return 0;
}
static int identity(int pid, struct row *r) {
    struct proc_bsdinfo info; memset(&info,0,sizeof(info)); errno=0;
    int n=proc_pidinfo(pid,PROC_PIDTBSDINFO,0,&info,sizeof(info));
    if (n!=(int)sizeof(info)) { if (!errno) errno=EPROTO; return -1; }
    memset(r,0,sizeof(*r));
    r->pid=(int)info.pbi_pid; r->ppid=(int)info.pbi_ppid; r->pgid=(int)info.pbi_pgid;
    r->uid=info.pbi_uid; r->ruid=info.pbi_ruid; r->svuid=info.pbi_svuid;
    r->fsuid=r->uid; /* macOS has no Linux fsuid; schema uses effective UID. */
    snprintf(r->state,sizeof(r->state),"%s",info.pbi_status==5 ? "Z" : "live");
    snprintf(r->start,sizeof(r->start),"%llu.%06llu",
             (unsigned long long)info.pbi_start_tvsec,(unsigned long long)info.pbi_start_tvusec);
    if (r->pid!=pid) { errno=EPROTO; return -1; }
    return 0;
}
static int absent(int pid) {
    /* Two complete native PID lists; no kill(pid,0) or name-only fallback. */
    for (int pass=0;pass<2;pass++) {
        int pids[MAX_ROWS]; size_t count=0;
        if (enumerate(pids,&count)) return 0;
        for (size_t i=0;i<count;i++) if (pids[i]==pid) return 0;
    }
    return 1;
}
static int paths(int pid, struct row *r, const char **operation) {
    struct proc_vnodepathinfo info; memset(&info,0,sizeof(info)); errno=0;
    *operation="proc_pidinfo-vnodepath";
    int n=proc_pidinfo(pid,PROC_PIDVNODEPATHINFO,0,&info,sizeof(info));
    if (n!=(int)sizeof(info)) { if (!errno) errno=EPROTO; return -1; }
    size_t length=strnlen(info.pvi_cdir.vip_path,sizeof(info.pvi_cdir.vip_path));
    if (!length || length==sizeof(info.pvi_cdir.vip_path)) { errno=EPROTO; return -1; }
    memcpy(r->cwd,info.pvi_cdir.vip_path,length+1);
    *operation="proc_pidpath"; errno=0;
    n=proc_pidpath(pid,r->exe,sizeof(r->exe));
    if (n<=0) { if (!errno) errno=EPROTO; return -1; }
    if (strnlen(r->exe,sizeof(r->exe))==sizeof(r->exe)) { errno=EOVERFLOW; return -1; }
    return 0;
}
#endif

static int census(void) {
    int pids[MAX_ROWS]; size_t count=0;
    if (enumerate(pids,&count)) return -1;
    for (size_t i=0;i<count;i++) {
        int pid=pids[i]; if (pid<=0) continue;
        if (deadline()) return -1;
        int settled=0;
        for (int attempt=0;attempt<3;attempt++) {
            struct row before,after;
            if (identity(pid,&before)) {
                int e=errno;
                if (absent(pid)) { raced++; settled=1; break; }
                return fail(pid,"native-identity",e);
            }
            int e=0; const char *operation="paths";
            if (owned(&before) && strcmp(before.state,"Z") && paths(pid,&before,&operation)) e=errno;
            if (identity(pid,&after)) {
                int identity_error=errno;
                if (absent(pid)) { raced++; settled=1; break; }
                return fail(pid,"native-revalidation",identity_error);
            }
            if (!same(&before,&after)) { reused++; continue; }
            if (e && strcmp(after.state,"Z")) {
                rows[row_count++]=before;
                return fail(pid,operation,e);
            }
            if (owned(&after) && strcmp(after.state,"Z")) {
                if (paths(pid,&after,&operation)) {
                    int path_error=errno;
                    struct row final;
                    if (identity(pid,&final)) {
                        if (absent(pid)) { raced++; settled=1; break; }
                        return fail(pid,"path-revalidation-identity",errno);
                    }
                    if (!same(&after,&final)) { reused++; continue; }
                    rows[row_count++]=after;
                    return fail(pid,operation,path_error);
                }
                if (strcmp(before.cwd,after.cwd) || strcmp(before.exe,after.exe)) { reused++; continue; }
                struct row final;
                if (identity(pid,&final)) {
                    int e_final=errno;
                    if (absent(pid)) { raced++; settled=1; break; }
                    return fail(pid,"final-native-revalidation",e_final);
                }
                if (!same(&after,&final)) { reused++; continue; }
            }
            snprintf(before.state,sizeof(before.state),"%s",after.state);
            if (row_count==MAX_ROWS) return fail(pid,"row-cap",EOVERFLOW);
            rows[row_count++]=before; settled=1; break;
        }
        if (!settled) return fail(pid,"changing-native-identity",EAGAIN);
    }
    return 0;
}
static void put(const char *s) {
    size_t n=strlen(s);
    if (output_size+n>=OUTPUT_CAP) { error_operation="output-cap"; error_number=EOVERFLOW; return; }
    memcpy(output+output_size,s,n); output_size+=n;
}
static void number(unsigned long long n) {
    char buf[32]; snprintf(buf,sizeof(buf),"%llu",n); put(buf);
}
static void quoted(const char *s) {
    put("\"");
    for (const unsigned char *p=(const unsigned char *)s;*p;p++) {
        char buf[8];
        if (*p=='"' || *p=='\\') { buf[0]='\\'; buf[1]=(char)*p; buf[2]=0; }
        else if (*p<32 || *p>=127) snprintf(buf,sizeof(buf),"\\u%04x",*p);
        else { buf[0]=(char)*p; buf[1]=0; }
        put(buf);
    }
    put("\"");
}
int main(int argc, char **argv) {
    if (clock_gettime(CLOCK_MONOTONIC,&begin)) return 2;
    if (argc!=2 || !argv[1][0] || strlen(argv[1])>10) return 2;
    for (const char *p=argv[1];*p;p++) if (*p<'0' || *p>'9') return 2;
    char *end=NULL; errno=0; unsigned long value=strtoul(argv[1],&end,10);
    if (errno || *end || value==0 || value>UINT_MAX) return 2;
    workload_uid=(unsigned)value;
    int result=census();
    put("{\"schema_version\":1,\"path_encoding\":\"byte-latin1\",\"workload_uid\":"); number(workload_uid);
    put(",\"observer_uid\":"); number(getuid()); put(",\"observer_euid\":"); number(geteuid());
    put(",\"observer_pid\":"); number((unsigned)getpid());
    put(",\"abi\":{\"pointer_size\":"); number(sizeof(void *));
#ifdef __APPLE__
    put(",\"bsd_size\":"); number(sizeof(struct proc_bsdinfo));
    put(",\"bsd_uid_offset\":"); number(offsetof(struct proc_bsdinfo,pbi_uid));
    put(",\"bsd_ruid_offset\":"); number(offsetof(struct proc_bsdinfo,pbi_ruid));
    put(",\"bsd_svuid_offset\":"); number(offsetof(struct proc_bsdinfo,pbi_svuid));
    put(",\"bsd_pid_offset\":"); number(offsetof(struct proc_bsdinfo,pbi_pid));
    put(",\"bsd_ppid_offset\":"); number(offsetof(struct proc_bsdinfo,pbi_ppid));
    put(",\"bsd_pgid_offset\":"); number(offsetof(struct proc_bsdinfo,pbi_pgid));
    put(",\"bsd_start_offset\":"); number(offsetof(struct proc_bsdinfo,pbi_start_tvsec));
    put(",\"vnode_size\":"); number(sizeof(struct proc_vnodepathinfo));
#endif
    put("},\"exited_revalidated\":"); number((unsigned)raced);
    put(",\"changed_revalidated\":"); number((unsigned)reused);
    put(",\"rows\":[");
    for (size_t i=0;i<row_count;i++) {
        const struct row *r=&rows[i]; if (i) put(",");
        put("{\"pid\":"); number((unsigned)r->pid); put(",\"ppid\":"); number((unsigned)r->ppid);
        put(",\"pgid\":"); number((unsigned)r->pgid); put(",\"uid\":"); number(r->uid);
        put(",\"ruid\":"); number(r->ruid); put(",\"svuid\":"); number(r->svuid);
        put(",\"fsuid\":"); number(r->fsuid); put(",\"start\":"); quoted(r->start);
        put(",\"state\":"); quoted(r->state);
        if (owned(r) && strcmp(r->state,"Z")) {
            put(",\"cwd\":"); quoted(r->cwd); put(",\"exe\":"); quoted(r->exe);
        }
        put("}");
    }
    put("],\"complete\":"); put(result==0 ? "true" : "false");
    put(",\"error\":");
    if (error_operation) {
        put("{\"pid\":"); number((unsigned)error_pid); put(",\"operation\":"); quoted(error_operation);
        put(",\"errno\":"); number((unsigned)error_number); put("}");
    } else put("null");
    put("}\n");
    if (error_operation && !strcmp(error_operation,"output-cap")) return 3;
    if (fwrite(output,1,output_size,stdout)!=output_size || fflush(stdout)) return 3;
    return result==0 ? 0 : 1;
}
