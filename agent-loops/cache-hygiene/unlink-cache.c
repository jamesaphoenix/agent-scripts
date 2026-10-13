/* Flat, retired, regenerable cache only. No traversal or file-stat enumeration. */
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#define QUEUE_SIZE 4096
static char queue[QUEUE_SIZE][NAME_MAX+1];
static size_t read_at,write_at,queued,removed,errors,unknown;
static int finished,dirfd_value;
static pthread_mutex_t mutex=PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t available=PTHREAD_COND_INITIALIZER;
static pthread_cond_t room=PTHREAD_COND_INITIALIZER;

static int eligible(const char *name) {
    size_t n=strlen(name);
    return !strcmp(name,".DS_Store") || (n>5 && !strcmp(name+n-5,".json")) || (n>4 && !strcmp(name+n-4,".png"));
}
static void *worker(void *unused) {
    (void)unused;
    for (;;) {
        char name[NAME_MAX+1];
        pthread_mutex_lock(&mutex);
        while(!queued && !finished)pthread_cond_wait(&available,&mutex);
        if(!queued && finished){pthread_mutex_unlock(&mutex);break;}
        strcpy(name,queue[read_at]);read_at=(read_at+1)%QUEUE_SIZE;queued--;
        pthread_cond_signal(&room);pthread_mutex_unlock(&mutex);
        int result=unlinkat(dirfd_value,name,0),failure=errno;
        pthread_mutex_lock(&mutex);
        if(!result){removed++;if(removed%10000==0){printf("removed %zu\n",removed);fflush(stdout);}}
        else if(failure!=ENOENT){errors++;if(errors<10)fprintf(stderr,"unlink error %s: %s\n",name,strerror(failure));}
        pthread_mutex_unlock(&mutex);
    }
    return NULL;
}
int main(int argc,char **argv) {
    char path[PATH_MAX];
    if(argc!=2 || !realpath(argv[1],path)){fprintf(stderr,"Need an existing retired cache path\n");return 2;}
    char prefix[PATH_MAX];
    const char *home=getenv("HOME");
    if(!home || snprintf(prefix,sizeof(prefix),"%s/.cache/tracelearn-mobile-readiness/staging/",home)>=(int)sizeof(prefix))return 2;
    if(strncmp(path,prefix,strlen(prefix)) || !strstr(path+strlen(prefix),".retired-") || strchr(path+strlen(prefix),'/')){
        fprintf(stderr,"Refusing a path outside a directly retired Studio cache\n");return 2;
    }
    struct stat st;
    if(lstat(path,&st) || !S_ISDIR(st.st_mode) || st.st_uid!=getuid()){fprintf(stderr,"Wrong cache ownership/type\n");return 2;}
    for(int pass=0;pass<5;pass++) {
        DIR *dir=opendir(path);if(!dir){perror("opendir");return 2;}
        dirfd_value=dirfd(dir);finished=0;unknown=0;
        size_t before=removed;
        pthread_t threads[8];
        for(int i=0;i<8;i++)if(pthread_create(&threads[i],NULL,worker,NULL)){fprintf(stderr,"Thread failure\n");return 2;}
        struct dirent *entry;
        errno=0;
        while((entry=readdir(dir))) {
            if(!strcmp(entry->d_name,".") || !strcmp(entry->d_name,".."))continue;
            if(!eligible(entry->d_name) || entry->d_type==DT_DIR){unknown++;continue;}
            pthread_mutex_lock(&mutex);
            while(queued==QUEUE_SIZE)pthread_cond_wait(&room,&mutex);
            strcpy(queue[write_at],entry->d_name);write_at=(write_at+1)%QUEUE_SIZE;queued++;
            pthread_cond_signal(&available);pthread_mutex_unlock(&mutex);
        }
        pthread_mutex_lock(&mutex);finished=1;pthread_cond_broadcast(&available);pthread_mutex_unlock(&mutex);
        for(int i=0;i<8;i++)pthread_join(threads[i],NULL);
        closedir(dir);
        printf("pass=%d removed=%zu unknown=%zu errors=%zu\n",pass,removed,unknown,errors);fflush(stdout);
        if(!rmdir(path)){printf("COMPLETE %zu\n",removed);return errors?1:0;}
        if(removed==before || unknown || errors)break;
    }
    fprintf(stderr,"Retained unexpected or unreadable cache entries\n");return 1;
}
