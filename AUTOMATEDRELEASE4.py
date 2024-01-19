from github import Github
import os
import base64
if not os.path.exists('python-files'):
    os.mkdir('python-files')

def print_repo_download(repo):
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
    try:
        ## for every file in repo
        for content in repo.get_contents(''):
            ##check if python file
            if content.path.endswith('.py'):
                #save the file
                filename=os.path.join('python-files',f'{repo.full_name.replace('/','-')}-{content.path}')
                ##writes file in binary
                with open(filename,'wb') as f:
                    f.write(content.decoded_content)
            print(content)
            ##repo license
        print('License: ', base64.b64decode(repo.get_license().content.encode()).decode())
    except Exception as e:
        print('Error: ',e)

username='finnveloz'
password='Learn9178@'
g=Github()
user=g.get_user(username)
for repo in user.get_repos():
    print_repo_download(repo)
    print('='*100)