from github import Github
import os
import base64
if not os.path.exists('python-files'):
    os.mkdir('python-files')

def print_repo(repo):
    print("Full Name: ", repo.full_name)

    print("Description: ", repo.description)
    print('Date created: ', repo.created_at)
    print('Date of last push: ',repo.pushed_at)
    print("Home Page:", repo.homepage)
    print('Language: ', repo.language)
    print('Number of forks:',repo.forks)
    print('Number of stars:',repo.stargazers_count)
    print('-'*50)
    print('Contents:')
   # for content in repo.get_contents(''):
        #print(content)
    try:
        for content in repo.get_contents(''):
            if content.path.endswith('.py'):
                filename=os.path.join('python-files',f'{repo.full_name.replace('/','-')}-{content.path}')
                with open(filename,'wb') as f:
                    f.write(content.decoded_content)
            print(content)
        print('License: ', base64.b64decode(repo.get_license().content.encode()).decode())
    except Exception as e:
        print('Error: ',e)

username='finnveloz'
password='Learn9178@'
g=Github()
user=g.get_user(username)
for repo in user.get_repos():
    print_repo(repo)
    print('='*100)