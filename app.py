from __future__ import print_function
# TODO: Make everything secure
from io import BytesIO

# app.py

from flask import Flask, render_template, request, redirect, url_for, flash, send_file, session, abort, \
    send_from_directory
from flask_login import (
    LoginManager, login_user, logout_user, login_required, current_user
)
from itsdangerous import URLSafeTimedSerializer
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import os
from werkzeug.datastructures import MultiDict
from itertools import groupby
import os
from werkzeug.utils import secure_filename

import json
import requests  # Add this import if not already present

import base64
import os.path
import smtplib
from asyncio import SendfileNotAvailableError
from PIL import Image

from email.mime.text import MIMEText

from google.auth.environment_vars import CREDENTIALS
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

from models import db, User, Story, Unit
from openai_client import call_openai




def unit_classes_dict_helper():
    d = {cls.__name__: cls for cls in Unit.__subclasses__()}
    return d

app = Flask(__name__)
app.secret_key = os.environ.get('FLASK_KEY') #  Needed for flashing messages

app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///tale_vortex.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

app.config['UPLOAD_FOLDER'] = 'static/uploads'


db.init_app(app)  # Initialize the database

login_manager = LoginManager(app)
login_manager.login_view = 'login'

serializer = URLSafeTimedSerializer(app.secret_key)




SCOPES = ["https://mail.google.com/"]
USER_TOKENS = "token.json"
CREDENTIALS = 'client_secret_gmail.json'


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

@app.route('/')
def index():
    if current_user.is_authenticated:
        user_stories = Story.query.filter_by(user_id=current_user.id).all()
        selected_story_id = session.get('current_story_id')
        selected_story = None
        grouped_units = {}
        if selected_story_id:
            selected_story = Story.query.get(selected_story_id)
            # Sort units by unit_type
            sorted_units = sorted(selected_story.units, key=lambda u: u.unit_type)
            # Group units by unit_type
            for unit_type, units in groupby(sorted_units, key=lambda u: u.unit_type):
                grouped_units[unit_type] = list(units)
        tp = render_template(
            'index.html',
            stories=user_stories,
            selected_story=selected_story,
            grouped_units=grouped_units,
            unit_classes_dict=unit_classes_dict_helper()
        )
        return tp
    else:
        return redirect(url_for('login'))


@app.route('/create_story', methods=['GET', 'POST'])
@login_required
def create_story():
    if request.method == 'POST':
        story_name = request.form.get('story_name').strip()
        setting_and_style = request.form.get('setting_and_style').strip()
        main_challenge = request.form.get('main_challenge').strip()

        errors = []

        if not story_name:
            errors.append("Please enter a story name.")
        if not setting_and_style:
            errors.append("Please provide the setting and style of your story.")
        if not main_challenge:
            errors.append("Please describe the main challenge, task, or goal.")

        if errors:
            for error in errors:
                flash(error)
            return render_template('create_story.html')

        # Check for duplicate story name for the user
        existing_story = Story.query.filter_by(user_id=current_user.id, name=story_name).first()
        if existing_story:
            flash(f"A story with the name '{story_name}' already exists.")
            return render_template('create_story.html')

        # Create the Story object with the new properties
        story = Story(
            name=story_name,
            user_id=current_user.id,
            setting_and_style=setting_and_style,
            main_challenge=main_challenge
        )
        db.session.add(story)
        db.session.commit()
        session['current_story_id'] = story.id
        flash(f"Story '{story_name}' has been created.")
        return redirect(url_for('index'))

    return render_template('create_story.html')

@app.route('/select_story/<int:story_id>')
@login_required
def select_story(story_id):
    story = Story.query.get_or_404(story_id)
    if story.user_id != current_user.id:
        abort(403)
    session['current_story_id'] = story.id
    flash(f"Story '{story.name}' has been selected.")
    # Redirect to the new story page
    return redirect(url_for('index'))


@app.route('/story/<int:story_id>/add_unit/<unit_type>', methods=['GET', 'POST'])
@login_required
def add_unit(story_id, unit_type):
    story = Story.query.get_or_404(story_id)
    if story.user_id != current_user.id:
        abort(403)

    if unit_type not in unit_classes_dict_helper():
        return "Invalid unit type", 400

    unit_class = unit_classes_dict_helper()[unit_type]
    feature_schema = unit_class.feature_schema

    fields = prepare_fields(feature_schema, story, edit_mode=False)  # Moved up to be used in both GET and POST

    if request.method == 'POST':
        action = request.form.get('action')
        form_data = request.form.to_dict(flat=False)
        if action == 'fill_features':
            # Handle the OpenAI API call
            description = request.form.get('unit_description', '').strip()

            try:
                msgs = feature_value_prefill_prompt(story, unit_type, description, feature_schema)
                prefilled_features = call_openai(messages=msgs)
                prefilled_features = json.loads(prefilled_features)
                errors = None
            except Exception as e:
                prefilled_features = None
                errors = [f"Error generating features: {e}"]

            if prefilled_features:
                # Update form_data with prefilled features
                for key, value in prefilled_features.items():
                    if key != 'name' and key != 'name_new':
                        form_data[key] = value

                # Keep the unit description in the form
                form_data['unit_description'] = description

                return render_template('add_unit.html', unit_type=unit_type, fields=fields, errors=[], form_data=MultiDict(form_data), story=story, edit_mode=False)
            else:
                return render_template('add_unit.html', unit_type=unit_type, fields=fields, errors=errors, form_data=request.form, story=story, edit_mode=False)

        elif action == 'save_unit':
            # Process form submission
            features, errors = process_form_submission(form_data, feature_schema, story, unit_name=None)

            # Validate 'name' field
            name = features.get('name', '').strip()
            if not name:
                errors.append("Name is required.")
            else:
                # Check for duplicate names in the same story
                existing_unit = story.get_unit_by_name(name)
                if existing_unit:
                    errors.append(f"A unit with the name '{name}' already exists in this story.")

            if errors:
                # Re-render the form with error messages
                fields = prepare_fields(feature_schema, story, edit_mode=False)
                temp = render_template('add_unit.html', unit_type=unit_type, fields=fields, errors=errors, form_data=MultiDict(form_data), story=story, edit_mode=False)
                return temp
            else:
                # Create the Unit and add to the database
                features['name'] = name  # Ensure name is set correctly
                unit = unit_class(unit_type=unit_type, features=features, story_id=story.id)
                db.session.add(unit)
                db.session.commit()

                # Save the updated undefined_names
                if name in story.undefined_names:
                    story.undefined_names.remove(name)
                db.session.add(story)
                db.session.commit()

                # After committing, check if the new unit resolves any undefined names
                update_references_with_new_unit(unit, story)

                flash(f"{unit_type} '{name}' has been added.")
                return redirect(url_for('index'))

    else:
        # GET request, render form
        fields = prepare_fields(feature_schema, story, edit_mode=False)
        temp = render_template('add_unit.html', unit_type=unit_type, fields=fields, errors=[], form_data=MultiDict(), story=story, edit_mode=False)
        return temp


@app.route('/story/<int:story_id>/download_json')
@login_required
def download_story_json(story_id):
    story = Story.query.get_or_404(story_id)
    if story.user_id != current_user.id:
        abort(403)

    # Generate the JSON data
    story_data = story.to_json()
    json_str = json.dumps(story_data, indent=4)

    # Create a BytesIO object
    json_bytes = BytesIO(json_str.encode('utf-8'))
    json_bytes.seek(0)  # Move to the beginning of the BytesIO object

    # Set the filename (ensure it's safe for file systems)
    from werkzeug.utils import secure_filename
    filename = secure_filename(f'{story.name}.json')

    # Send the file
    return send_file(
        json_bytes,
        as_attachment=True,
        download_name=filename,
        mimetype='application/json'
    )

def prepare_fields(feature_schema, story, edit_mode=False):
    fields = []
    for feature_name, expected_type in feature_schema.items():
        field = {'name': feature_name}
        if feature_name == 'name':
            if edit_mode:
                field['type'] = 'str'
            else:
                field['type'] = 'unitname'
                field['options'] = [(name, name) for name in story.undefined_names]
            field['required'] = True
        else:
            if expected_type == bool:
                field['type'] = 'bool'
            elif expected_type == float:
                field['type'] = 'float'
            elif expected_type == str:
                field['type'] = 'str'
            elif expected_type == int:
                field['type'] = 'int'
                raise ValueError  # this should never happen
            elif expected_type == list:
                field['type'] = 'list'
                # Add undefined names to options
                field['options'] = [
                    (unit.name, unit.name)
                    for unit in story.units
                ] + [(name, name) for name in story.undefined_names]
            else:
                field['type'] = 'unknown'
        fields.append(field)
    return fields

@app.route('/story/<int:story_id>/download')
@login_required
def download_story(story_id):
    story = Story.query.get_or_404(story_id)
    if story.user_id != current_user.id:
        abort(403)

    # Generate the PDF file
    filename = f'pdf_files/story_{story.id}.pdf'
    story.to_pdf(filename)

    # Send the file to the client
    return send_file(
        filename,
        as_attachment=True,
        mimetype='application/pdf'
    )

@app.route('/story/<int:story_id>/edit_unit/<unit_name>', methods=['GET', 'POST'])
@login_required
def edit_unit(story_id, unit_name):
    story = Story.query.get_or_404(story_id)
    if story.user_id != current_user.id:
        abort(403)

    unit = story.get_unit_by_name(unit_name)
    if not unit:
        abort(404, description="Unit not found.")

    unit_class = type(unit)
    unit_type = unit.unit_type
    feature_schema = unit_class.feature_schema

    if request.method == 'POST':
        # Process form submission
        action = request.form.get('action')
        form_data = request.form.to_dict(flat=False)
        if action == 'fill_features':
            # Handle the OpenAI API call
            description = request.form.get('unit_description', '').strip()

            try:
                msgs = feature_value_prefill_prompt(story, unit_type, description, feature_schema)
                prefilled_features = call_openai(messages=msgs)
                prefilled_features = json.loads(prefilled_features)
                errors = None
            except Exception as e:
                prefilled_features = None
                errors = [f"Error generating features: {e}"]

            if prefilled_features:
                # Update form_data with prefilled features
                for key, value in prefilled_features.items():
                    if key != 'name' and key != 'name_new':
                        form_data[key] = value

                # Keep the unit description in the form
                form_data['unit_description'] = description

                return render_template('add_unit.html', unit_type=unit_type, fields=prepare_fields(feature_schema, story, edit_mode=True), errors=[], form_data=MultiDict(form_data), story=story, edit_mode=True)
            else:
                return render_template('add_unit.html', unit_type=unit_type, fields=prepare_fields(feature_schema, story, edit_mode=True), errors=errors, form_data=request.form, story=story, edit_mode=True)

        elif action == 'save_unit':
            # Process form submission
            features, errors = process_form_submission(form_data, feature_schema, story, unit_name=unit.name, edit_mode=True)

            # Validate 'name' field
            new_name = features.get('name', '').strip()
            if not new_name:
                errors.append("Name is required.")
            else:
                # Check for duplicate names in the same story (excluding current unit)
                existing_unit = story.get_unit_by_name(new_name)
                if existing_unit and existing_unit.id != unit.id:
                    errors.append(f"A unit with the name '{new_name}' already exists in this story.")

            if errors:
                # Re-render the form with error messages
                fields = prepare_fields(feature_schema, story, edit_mode=True)
                return render_template('add_unit.html', unit_type=unit_type, fields=fields, errors=errors, form_data=form_data, story=story, edit_mode=True)
            else:
                # Update the unit's features
                features['name'] = new_name  # Ensure name is set correctly
                unit.features = features
                db.session.add(unit)
                db.session.add(story)
                db.session.commit()

                update_references_with_new_unit(unit, story)

                refresh_undefined_names(story)

                flash(f"{unit_type} '{new_name}' has been updated.")
                return redirect(url_for('index'))

    else:
        # GET request, render form with existing data
        fields = prepare_fields(feature_schema, story, edit_mode=True)
        form_data = MultiDict(unit.features)

        return render_template('add_unit.html', unit_type=unit_type, fields=fields, errors=[], form_data=form_data, story=story, edit_mode=True)


@app.route('/story/<int:story_id>/create_board_game', methods=['GET', 'POST'])
@login_required
def create_board_game(story_id):
    story = Story.query.get_or_404(story_id)
    if story.user_id != current_user.id:
        abort(403)

    if request.method == 'POST':
        # Handle form submission
        texts = {}
        images = {}

        # Process text fields
        for key, value in request.form.items():
            texts[key] = value

        # Process uploaded images
        for key in request.files:
            file = request.files[key]
            if file and file.filename != '':
                filename = secure_filename(file.filename)
                file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
                images[key] = filename
            else:
                # If no new image was uploaded, keep the existing one
                existing_image = request.form.get(f"{key}_existing")
                if existing_image:
                    images[key] = existing_image

        # Save the texts and images to the story
        story.board_game_data = {
            'texts': texts,
            'images': images
        }
        db.session.commit()
        flash('Board Adventure Game data has been saved.')
        return redirect(url_for('index'))

    else:
        # Generate prefilled data using ChatGPT
        prompt = story.create_board_game_prompt()
        messages = [
            {"role": "system",
             "content": "You are an assistant that helps creating a board adventure game. Your job is to extract information out of the story and return relevant pieces in a JSON."},
            {"role": "user", "content": prompt}
        ]
        try:
            assistant_reply = call_openai(messages)
            prefilled_data = json.loads(assistant_reply)
        except Exception as e:
            print(f"Error generating prefilled data: {e}")
            prefilled_data = {}

        # Now generate images for each location and NPC
        # Ensure the UPLOAD_FOLDER exists
        os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

        # Generate images for locations
        for location in prefilled_data.get('locations', []):
            location_name = location.get('name', 'Unknown Location')
            # Create a prompt for the image generation
            image_prompt = f"An illustration of {location_name} in the setting of {story.setting_and_style}."
            # Generate a secure filename
            filename = secure_filename(f"location_{location_name}.png")
            # Generate and save the image
            image_filename = generate_and_save_image(image_prompt, filename)
            # Add the image filename to the location data
            if image_filename:
                location['image_filename'] = image_filename

        # Generate images for NPCs
        for npc in prefilled_data.get('npcs', []):
            npc_name = npc.get('name', 'Unknown NPC')
            # Create a prompt for the image generation
            image_prompt = f"A portrait of {npc_name} in the style of {story.setting_and_style}."
            # Generate a secure filename
            filename = secure_filename(f"npc_{npc_name}.png")
            # Generate and save the image
            image_filename = generate_and_save_image(image_prompt, filename)
            # Add the image filename to the NPC data
            if image_filename:
                npc['image_filename'] = image_filename

        temp = render_template('create_board_game.html', prefilled_data=prefilled_data, story=story)
        return temp


@app.route('/story/<int:story_id>/download_npc_pdf')
@login_required
def download_npc_pdf(story_id):
    story = Story.query.get_or_404(story_id)
    if story.user_id != current_user.id:
        abort(403)

    output_path = f'static/exports/{secure_filename(story.name)}_npcs.pdf'
    story.generate_npc_pdf(output_path)

    return send_from_directory(directory='static/exports', path=f'{secure_filename(story.name)}_npcs.pdf', as_attachment=True)

@app.route('/story/<int:story_id>/download_location_pdf')
@login_required
def download_location_pdf(story_id):
    story = Story.query.get_or_404(story_id)
    if story.user_id != current_user.id:
        abort(403)

    output_path = f'static/exports/{secure_filename(story.name)}_locations.pdf'
    story.generate_location_pdf(output_path)

    return send_from_directory(directory='static/exports', path=f'{secure_filename(story.name)}_locations.pdf', as_attachment=True)

@app.route('/story/<int:story_id>/download_additional_text_pdf')
@login_required
def download_additional_text_pdf(story_id):
    story = Story.query.get_or_404(story_id)
    if story.user_id != current_user.id:
        abort(403)

    output_path = f'static/exports/{secure_filename(story.name)}_additional_texts.pdf'
    story.generate_additional_text_pdf(output_path)

    return send_from_directory(directory='static/exports', path=f'{secure_filename(story.name)}_additional_texts.pdf', as_attachment=True)



def generate_and_save_image(prompt, filename):
    with Image.open('default_image.jpg') as img:
        filename = secure_filename(filename)
        img.save(os.path.join('static/uploads/', filename))
        return filename
    try:
        response = openai.Image.create(
            prompt=prompt,
            n=1,
            size="512x512",
        )
        image_url = response['data'][0]['url']
        image_data = requests.get(image_url).content
        image_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        with open(image_path, 'wb') as f:
            f.write(image_data)
        return filename  # Return the filename to use it later
    except Exception as e:
        print(f"Error generating image: {e}")
        return None


def send_login_email(email, token):
    login_link = url_for('login_with_token', token=token, _external=True)
    subject = "Your Login Link for Tale Vortex"
    message = f"""Hello,

Click the link below to log in to Story Creator:

{login_link}

This link will expire in 1 hour.

If you did not request this email, please ignore it.

Best regards,
Tale Vortex Team"""

    host = "smtp.gmail.com"
    port = 587

    user = os.getenv('USER_EMAIL')
    sender = user
    recipients = [email]
    try:
        send_emails(host, port, subject, message, sender, recipients)
        print(f"Email sent successfully")
        print(f'Email Address: {email}')
        print(f'Secure Link: {login_link}')
    except Exception as e:
        print(f"Error sending email: {e}")
        print(f'Email Address: {email}')
        print(f'Secure Link: {login_link}')

    '''
    # Replace these with your SMTP server details or use environment variables
    SMTP_SERVER = os.environ.get('SMTP_SERVER', 'smtp.example.com')
    SMTP_PORT = int(os.environ.get('SMTP_PORT', 587))
    SMTP_USERNAME = os.environ.get('SMTP_USERNAME')
    SMTP_PASSWORD = os.environ.get('SMTP_PASSWORD')

    if not SMTP_USERNAME or not SMTP_PASSWORD:
        print("SMTP_USERNAME and SMTP_PASSWORD environment variables must be set.")
        print(f'Email: {email}')
        print(f'Link: {login_link}')
        return

    # Prepare the email
    msg = MIMEMultipart()
    msg['Subject'] = subject
    msg['From'] = SMTP_USERNAME
    msg['To'] = email
    msg.attach(MIMEText(body, 'plain'))

    try:
        # Send the email via SMTP server
        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        server.starttls()
        server.login(SMTP_USERNAME, SMTP_PASSWORD)
        server.sendmail(SMTP_USERNAME, email, msg.as_string())
        server.quit()
    except Exception as e:
        print(f"Error sending email: {e}")
        print(f'Email Address: {email}')
        print(f'Secure Link: {login_link}')
    '''


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email').strip()

        if not email:
            flash('Please enter your email address.')
            return redirect(url_for('login'))

        # Generate a token
        token = serializer.dumps(email, salt='login')

        # Send the email with the token
        send_login_email(email, token)

        flash('A login link has been sent to your email address.')
        return redirect(url_for('index'))

    return render_template('login.html')


@app.route('/login/<token>')
def login_with_token(token):
    try:
        # The token expires after 1 hour (3600 seconds)
        email = serializer.loads(token, salt='login', max_age=3600)
    except Exception as e:
        flash('The login link is invalid or has expired.')
        return redirect(url_for('login'))

    user = User.query.filter_by(email=email).first()
    if not user:
        user = User(email=email)
        db.session.add(user)
        db.session.commit()
    login_user(user)

    flash('You have been logged in.')
    return redirect(url_for('index'))


@app.route('/logout')
@login_required
def logout():
    logout_user()
    session.pop('current_story_id', None)  # Clear the selected story
    flash('You have been logged out.')
    return redirect(url_for('index'))


def update_references_with_new_unit(unit, story):
    new_unit_name = unit.name
    if new_unit_name in story.undefined_names:
        for u in story.units:
            # Skip the unit we just added
            if u.name == new_unit_name:
                continue
            updated = False
            for key, value in u.features.items():
                # Skip updating the 'name' field
                if key == 'name':
                    continue  # Do not update the 'name' field
                if value == new_unit_name:
                    updated = True
                elif isinstance(value, list):
                    # Replace any occurrences in lists
                    new_list = []
                    for item in value:
                        if item == new_unit_name:
                            new_list.append(new_unit_name)
                            updated = True
                        else:
                            new_list.append(item)
                    u.features[key] = new_list
            if updated:
                db.session.add(u)
        # Remove the resolved name from undefined_names
        story.undefined_names.remove(new_unit_name)
        db.session.add(story)
        db.session.commit()


def process_form_submission(form_data, feature_schema, story, unit_name=None, edit_mode=False):
    features = {}
    errors = []


    for feature_name, expected_type in feature_schema.items():
        if feature_name == 'name':
            if edit_mode:
                name = form_data.get(feature_name, [''])[0].strip()
            else:
                selected_name = form_data.get(feature_name, [''])[0].strip()
                new_name = form_data.get(f"{feature_name}_new", [''])[0].strip()
                if not selected_name and not new_name:
                    errors.append("Please select a name from the dropdown or provide a new name.")
                name = new_name if new_name else selected_name
            features[feature_name] = name
            continue

        value = form_data.get(feature_name, [''])[0]
        new_value = form_data.get(f"{feature_name}_new", [''])[0].strip()

        if expected_type == bool:
            features[feature_name] = (value == 'on')
        elif expected_type == float:
            try:
                features[feature_name] = float(value) if value else 0.0
            except ValueError:
                errors.append(f"Invalid value for {feature_name}.")
                features[feature_name] = 0.0
        elif expected_type == str:
            features[feature_name] = value.strip() if value else ''
        elif expected_type == int:
            try:
                features[feature_name] = int(value) if value else 1
            except ValueError:
                errors.append(f"Invalid value for {feature_name}.")
                features[feature_name] = 1
        elif expected_type == list:
            selected_values = form_data.get(feature_name)
            if not selected_values:
                selected_values = []
            new_values = form_data.get(feature_name + '_new', '')[0].split(', ')

            selected_and_new_values = selected_values + new_values
            if '' in selected_and_new_values:
                selected_and_new_values.remove('')
                if '' in selected_and_new_values:
                    selected_and_new_values.remove('')
            combined_values = []

            # Add existing unit names
            for v in selected_and_new_values:
                related_unit = story.get_unit_by_name(v.strip())
                if related_unit:
                    combined_values.append(related_unit.name)
                else:
                    combined_values.append(v.strip())
                    if combined_values[-1] not in story.undefined_names:
                        story.undefined_names.append(combined_values[-1])
                        db.session.add(story)
            db.session.commit()
            features[feature_name] = combined_values
        else:
            # Unsupported type
            errors.append(f"Unsupported type for {feature_name}.")
            features[feature_name] = value

    return features, errors

def refresh_undefined_names(story):
    # Recalculate undefined names based on current units
    all_referenced_names = set()
    existing_unit_names = set(unit.name for unit in story.units)

    for unit in story.units:
        for feature_name, value in unit.features.items():
            if isinstance(value, str):
                if value not in existing_unit_names and value:
                    all_referenced_names.add(value)
            elif isinstance(value, list):
                for item in value:
                    if item not in existing_unit_names and item:
                        all_referenced_names.add(item)
    story.undefined_names = list(all_referenced_names)
    db.session.add(story)
    db.session.commit()


def getToken():
    creds = None

    if os.path.exists(USER_TOKENS):
        creds = Credentials.from_authorized_user_file(USER_TOKENS, SCOPES)
        creds.refresh(Request())

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS, SCOPES)
            creds = flow.run_local_server(port=5000)
        with open(USER_TOKENS, 'w') as token:
            token.write(creds.to_json())

    return creds.token

def generate_oauth2_string(username, access_token):
    auth_string = f'user={username}\1auth=Bearer {access_token}\1\1'
    return base64.b64encode(auth_string.encode('ascii')).decode('ascii')

def send_emails(host, port, subject, msg, sender, recipients):
    token = getToken()
    auth_string = generate_oauth2_string(sender, token)

    if 'melody.bocha@gmail.com' in recipients:
        raise ValueError('Melody does not get emails.')
    if recipients[0].endswith('test.de'):
        raise ValueError('This was only a test email.')

    msg = MIMEText(msg)
    msg['Subject'] = subject
    msg['From'] = sender
    msg['To'] = ', '.join(recipients)

    server = smtplib.SMTP(host, port)
    server.starttls()
    server.docmd('AUTH', 'XOAUTH2 ' + auth_string)
    server.sendmail(sender, recipients, msg.as_string())
    server.quit()


def feature_value_prefill_prompt(story, unit_type, description, feature_schema):
    if not description:
        description = f"The {unit_type} should fit well within the story."
    prompt = f"""Based on the following story information and the description, please provide values for the features of the unit type '{unit_type}' in JSON format.
A Unit is an element of the story.

Story Setting and Style:
{story.setting_and_style}

Main Challenge:
{story.main_challenge}

All already existing units of the story:
{story.to_text_list()}

Description of the {unit_type} to create:
{description}

Please provide a JSON object with the following keys and appropriate values:

"""

    # List the features
    prompt += "Features:\n"
    for feature_name, expected_type in feature_schema.items():
        prompt += f"- '{feature_name}' ({expected_type.__name__ if not isinstance(expected_type, tuple) else 'list'}): {expected_type}\n"

    prompt += """

Example response:
{
    "name": "Name of the unit",
    "feature1": "value1",
    "feature2": true,
    "feature3": 0.5,
    "feature4": [123, 456],
    "feature5": "Some description"
}

Please ensure the response is valid JSON, starting with the first opening bracket "{" and ending with the last closing bracket "}".
Do not use the word "json" or quotation marks of any kind outside the json.
"""

    messages = [
        {"role": "system",
         "content": "You are an assistant that helps fill out feature values for units in a story based on a description."},
        {"role": "user", "content": prompt}
    ]
    return messages

if __name__ == '__main__':
    with app.app_context():
        db.create_all()  # Create tables if they don't exist
    app.run(debug=False, host="0.0.0.0")
