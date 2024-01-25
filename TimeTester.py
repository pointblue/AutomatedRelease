from github import Github
from github import GithubException
import base64
import subprocess
import re
from optparse import OptionParser

def git_version():
    pass
    #p = subprocess.Popen(["git", "log" , '-1', '--date=iso'], stdout=subprocess.PIPE)
    #out, err = p.communicate()
    #m = re.search('\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}', out)
    #return m.group(0)

def attempt2():
    orgname = 'pointblue'

    ##github object
    github_token='FinnV1'
    g = Github()

    org = g.get_organization(orgname)
    ##for every repo in the users repo, use the print_repo function
    i = 0
    outputlist = []
    # for each repo in the org, print out
    for repo in org.get_repos():
        print((str(repo.full_name)))
        ##assigns the repos full name to the repo_name variable
        repo_name = repo.full_name
        ##assigns the string of the url of the repo to the repo_url variable
        repo_url = f'https://github.com/{repo_name}.git'
        ##opens the subprocess module, passing the git arguemnt, ls-remote which specifices that information is recieved from remote source
        ##passes the url of the repo
        ##then a pipe is created to capture output, allowing python to read it
        ##the output of git ls-remote on the url given by the repo_variable
        process = subprocess.Popen(["git", "ls-remote", repo_url], stdout=subprocess.PIPE)

        stdout, stderr = process.communicate()
        ##splits  sha from the other reference information outputed, using stdout,decode to convert neccesary information into text, -1 to hopefully get last commit date in list
        sha = re.split(r'\t+', stdout.decode('ascii'))[-1]
        #print(sha)



        ##using the sha, get a specific repo, acsess the commit, the author, and then retrieve the date
        last_commit_date = repo.get_commit(sha).commit.author.date

        # Print the repo name and its last commit date
        print(f"Repo: {repo_name}, Last Commit Date: {last_commit_date}")
        print(f'=' * 50)
        ##RATE LIMITER
        #if (i == 1):
            #break
        #i += 1
def attempt3 ():
    orgname = 'pointblue'
    ##github object
    #github_token = 'FinnV1'
    g = Github()
    org = g.get_organization(orgname)
    ##for every repo in the users repo, use the print_repo function
    i = 0
    # for each repo in the org, print out
    for repo in org.get_repos():
        #print_repo(repo)
        ##divides each repo section in output
        print('=' * 100)
        tree = repo.heads.master.commit.tree
        for blob in tree:
            commit = next(repo.iter_commits(paths=blob.path, max_count=1))
            print(blob.path, commit.committed_date)

        if(i==0):
            break
        i+=1



attempt2()
#attempt3()
#usage = "usage: %prog -f filepath"
#parser = OptionParser(usage=usage)
#parser.add_option("-f", default='version.py')
#(options, args) = parser.parse_args()
#path = options.f
#with open(path, 'w+') as f:
    #f.write(git_version())
