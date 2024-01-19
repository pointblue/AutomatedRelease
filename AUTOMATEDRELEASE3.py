from github import Github
from github import GithubException
def print_repo(repo):
    #prints full name of repo
    print("Full Name: ", repo.full_name)
    ##description of repo
    print("Description: ", repo.description)
    ##date repo created
    print('Date created: ', repo.created_at)
    ##date of last git push
    print('Date of last push: ',repo.pushed_at)
    ## home website
    print("Home Page:", repo.homepage)
    ##programming language
    print('Language: ', repo.language)
    #number of forks
    print('Number of forks:',repo.forks)
    #number of stars
    print('Number of stars:',repo.stargazers_count)
    print('-'*50)
    print('Contents:')
    #for every file/directory in the repo, print out
    for content in repo.get_contents(""):
        print(content)


    try:
        #license of repo if has
        print('License: ', base64.b64decode(repo.get_license().content.encode()).decode())
    except:
        pass
## username variable
username='finnveloz'
#password variable
password='Learn9178@'
##github object
g=Github()

user=g.get_user(username)
##for every repo in the users repo, use the print_repo function
for repo in user.get_repos():
    print_repo(repo)
    ##divides each repo section in output
    print('='*100)
#for repo in g.search_repositories('python-tutorial'):
    #print_repo(repo)