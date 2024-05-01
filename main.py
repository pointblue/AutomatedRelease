import re
import subprocess

from github import Github
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
import os



def get_sprintdates():
    today = datetime.now(timezone.utc)



    first_day_of_year = datetime(today.year, 1, 1, tzinfo=timezone.utc)
    full_weeks_since_year_start = (today - first_day_of_year).days // 7

    ## We add a plus one because the integer divison can only find what week you are in by measuring how many full weeks have passed,
    ## and so we still need to account for the current week we are in
    week_number =full_weeks_since_year_start + 1



    ##gets find_right_monday to right week
    find_right_monday = first_day_of_year + timedelta(weeks=week_number)

    ##gets find_right_monday to the right monday(this shouldnt matter in 2024, i added in case other years mess somthing up)
    find_right_monday = find_right_monday - timedelta(days=find_right_monday.weekday())

    ##moves to correct monday depending on whether it is even or odd week
    find_right_monday -= timedelta(weeks=4) if week_number%2==0 else timedelta(weeks=3)

    sprint_start = find_right_monday
    sprint_end = sprint_start + timedelta(days=4) + timedelta(weeks=1)

    print(f'Week Number: {week_number}')
    return sprint_start, sprint_end

def get_bullet_points(full_description,i):
    bullet_message=full_description[i]

    if(full_description[i+1][0]=='-'):

        for paragraph in (full_description[i+1:]):

            if paragraph[0]=='-':
                bullet_message+='\n'
                bullet_message+=(paragraph)
            else:
                break
    if(len(bullet_message)==0):
        return False
    else:

        return bullet_message







def get_first_paragraph(full_description):

    commit_message=full_description[0]

    ## checks if chosen paragraph starts with a hash or is only a special charachter, then moves on to next element of full_description until correct message chosen
    for i,paragraph in enumerate(full_description):

        if not(paragraph[0]=='#' or paragraph=='\n' or paragraph=='\r'):


            if get_bullet_points(full_description,i):
                commit_message=paragraph
                
                commit_message+=get_bullet_points(full_description,i)
            else:

                commit_message=paragraph
            break

    ## before full_description passed to this function
    #split('\n') used on it
    # the split does not handle consecutive '\n's very well,
    # causing them to either appear as their own element, or as the first charachters in a string
    #example is '\nexamplestring' or '\n' as its own element
    if(commit_message[0:1]=='\n' or commit_message[0:1]=='\r'):
        commit_message=commit_message[1:]


    return commit_message





def fetch_commits_within_sprint(repo, sprint_start_date, sprint_end_date):
    sprint_commits = []
    errorlist=[]


    try:
        dev_branch = repo.get_branch("dev")

        commits = repo.get_commits(since=sprint_start_date, until=sprint_end_date, sha=dev_branch.commit.sha)
        for commit in commits:
            commit_date = commit.commit.author.date

            for pull in commit.get_pulls():
                ## gets array of paragraphs
                full_description=pull.body.split('\n')
                commit_title=[commit.commit.message.split('\n')[0]]
                pr_link = pull.html_url

                if(full_description):
                    first_paragraph=get_first_paragraph(full_description)
                    message=first_paragraph
                else:
                    message=f'{commit_title} :  {pr_link}'


                if('Merge'in commit_title[0]):
                    sprint_commits.append(('dev', commit_date, commit_title,message,pr_link))





    except Exception as e:
        ##outputs list of repos where no commits found, no dev branch
        errorlist.append((repo.full_name,e))
    return sprint_commits,errorlist



def print_commits():


    orgname = 'pointblue'
    errorlist=[]
    load_dotenv()

    github_token = os.getenv('GITHUB_TOKEN')
    ###if the program can't find github token, it checks if path to dotenv file exists, if not, then it creates one and configures it


    sprint_start_date, sprint_end_date = get_sprintdates()




    print(f'Current sprint: {sprint_start_date.strftime("%Y-%m-%d")} to {sprint_end_date.strftime("%Y-%m-%d")}')
    if not github_token:
        envpath = os.path.exists('env')
        print('No Github Token')
        if not (envpath):
            with open('.env', 'w') as fh:
                github_token = input('Please enter your GitHub personal access token so that it can be stored for later use: ')
                fh.write(f'GITHUB_TOKEN={github_token}')
                print('If you are pushing commits to this code, remember to add your .env file to .gitignore')

        else:
            print('Please add your GitHub token to your dotenv file')

    g = Github(github_token)
    org = g.get_organization(orgname)

    for repo in org.get_repos():
        try:
            ##stores the date, title,and branch in commits in branch dev from sprint period
            out,errors = fetch_commits_within_sprint(repo, sprint_start_date, sprint_end_date)
            team=org.get_team_by_slug('Deployers')
            permission=team.get_repo_permission(repo)

            ##appends errorlist with error messages that are not empty
            if(errors):
                errorlist.extend(errors)
            ## This should ensure only branches where the user has pull permissions are printed out
            if (out and permission.pull==True):


                print('='*50)
                print(f"Repository: {repo.full_name}")
                for branch_name, commit_date, commit_title,commit_message,pr_link in out:
                    formatted_date = commit_date.strftime('%Y-%m-%d %H:%M:%S %Z')
                    #if('Merge' in commit_title[0]):
                    print('_' * 50)

                    print(f'Branch: {branch_name}')
                    print(f'Date: {formatted_date}')

                    print(f'Title: {commit_title[0]}')
                    print(f'Description: \n{commit_message}')
                    print(f'Link: {pr_link}')
                    print('_' * 50)


                print('=' * 50)

        except Exception as e:
            print(f'Error fetching  for repo  {repo.full_name} : {e}')
    for repo,error in errorlist:
        if isinstance(error, Exception):
            if hasattr(error, 'data') and 'message' in error.data:
                error_message = error.data['message']
                if not "Branch not found" in error_message:
                    print(f'Error in repository {repo}: {error}')
        else:

            pass




        # Check if there are branches with no commits in the last two weeks and update last_commit_date
print_commits()
print('END')


