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
orgname='pointblue'


##github object
g=Github()

org=g.get_organization(orgname)
##for every repo in the users repo, use the print_repo function
i=0
#for each repo in the org, print out
for repo in org.get_repos():



    print_repo(repo)
    ##divides each repo section in output
    print('='*100)

