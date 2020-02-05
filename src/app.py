import tensorflow as tf
import torch
import torch.nn as nn
from torchvision import models
import torchvision.transforms as transforms
from sklearn.metrics.pairwise import cosine_similarity
from PIL import Image
from werkzeug.utils import secure_filename
import pathlib

import io
import numpy as np
import pandas as pd
import os
import sys

from flask import Flask, jsonify, request, flash, redirect, url_for, render_template, send_from_directory

app = Flask(__name__)

# Define dirs and requirements
total_brands = ['Louis Vuitton', 'Chanel', 'Coach', 'Michael Kors', 'Gucci', 'Longchamp', 'Other', 'None']
brands = [total_brands[i] for i in [1,2,0,6,1,5,4,4,1,3,3,0,0,7,0,3,4,0,4]]
names = ['2.55','F57842', 'alma','handbag','boy','classic','dionysus','ggmarmont','grandshoppingtote','jetsettote','mercertotebag',
'neverfull','newwavechaintote','None','petiteboitechapeau','selmasatchel','sohodisco','speedy','sylvie']
websites = ["https://www.chanel.com/en_WW/fashion.html#search/2.55",
"https://www.coach.com/",
"https://us.louisvuitton.com/eng-us/search/alma",
"None",
"https://www.chanel.com/en_WW/fashion.html#search/BOY/",
"https://us.longchamp.com/products/le-pliage/tote-bag-s/l2605089545",
"https://www.gucci.com/us/en/st/capsule/dionysus-collection",
"https://www.gucci.com/us/en/st/capsule/gg-marmont-collection",
"https://www.chanel.com/us/fashion/p/A91046Y09958N5024/large-shopping-bag-calfskin-silver-tone-metal/",
"https://www.michaelkors.com/search/_/Rtt-jet+set+",
"https://www.michaelkors.com/search/_/Rtt-mercer+",
"https://us.louisvuitton.com/eng-us/search/neverfull",
"https://us.louisvuitton.com/eng-us/search/new%20wave%20chain",
"None",
"https://us.louisvuitton.com/eng-us/products/petite-boite-chapeau-monogram-nvprod670013v",
"https://www.michaelkors.com/search/_/Rtt-selma+satchel",
"https://www.gucci.com/us/en/st/newsearchpage?searchString=soho%20disco&search-cat=header-search",
"https://us.louisvuitton.com/eng-us/search/speedy",
"https://www.gucci.com/us/en/st/newsearchpage?searchString=sylvie&search-cat=header-search"
]

UPLOAD_FOLDER = '../data/example'
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
ALLOWED_EXTENSIONS = { 'png', 'jpg', 'jpeg'}
tf.gfile = tf.io.gfile

# Model loading and image preprocessing (crop)
def load_detection_model(model_name):
    """
    Load detection model from tensorflow
    
    Parameters
    ----------
    model_name : str
        model name that will be loaded, can be found in 
        /Users/jianinglu1/Documents/GitHub/models/research/object_detection/g3doc/detection_model_zoo.md

    Returns
    -------
    model
        detection model
    """
    base_url = 'http://download.tensorflow.org/models/object_detection/'
    model_file = model_name + '.tar.gz'
    model_dir = tf.keras.utils.get_file(
        fname=model_name, 
        origin=base_url + model_file,
        untar=True)

    model_dir = pathlib.Path(model_dir)/"saved_model"

    model = tf.saved_model.load(str(model_dir))
    model = model.signatures['serving_default']

    return model

def load_model():
    """
    Load the identification model for handbags
    
    Returns
    -------
    model_ft
        whole model for predict brand and type
    model_test
        model for get the handbag vector (remove the last layer of whole model)
    """
    model_ft = models.resnet50(pretrained=True)
    num_ftrs = model_ft.fc.in_features
    # Here the size of each output sample is set to 2.
    # Alternatively, it can be generalized to nn.Linear(num_ftrs, len(class_names)).
    model_ft.fc = nn.Linear(num_ftrs, 19)
    device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")
    model_ft = model_ft.to(device)
    model_name = torch.load("../models/model_name_resnet50gpu_correct_merged", map_location=torch.device('cpu'))
    model_ft.load_state_dict(model_name)
    model_ft.eval()
    # get the model for extracting the handbag vectors
    model_test = nn.Sequential(*list(model_ft.children())[:-1])
    model_test.eval()
    return model_ft, model_test

def run_inference_for_single_image(model, image):
    """
    Get object detection results
    
    Parameters
    ----------
    model : object
        detection model
    image : image object
        input image
    
    Returns
    -------
    dictionary
        object detection results
    """
    image = np.asarray(image)
    # The input needs to be a tensor, convert it using `tf.convert_to_tensor`.
    input_tensor = tf.convert_to_tensor(image)
    # The model expects a batch of images, so add an axis with `tf.newaxis`.
    input_tensor = input_tensor[tf.newaxis,...]

    # Run inference
    output_dict = model(input_tensor)

    # All outputs are batches tensors.
    # Convert to numpy arrays, and take index [0] to remove the batch dimension.
    # We're only interested in the first num_detections.
    num_detections = int(output_dict.pop('num_detections'))
    output_dict = {key:value[0, :num_detections].numpy() 
                 for key,value in output_dict.items()}
    output_dict['num_detections'] = num_detections

    # detection_classes should be ints.
    output_dict['detection_classes'] = output_dict['detection_classes'].astype(np.int64)

    return output_dict

def crop_image(image_bytes, model):
    """
    Crop image based on object detection results and only keep the handbag part of image
    
    Parameters
    ----------
    image_bytes : input image, read from input image file
        input image
    model : object
        detection model
    
    Returns
    -------
    img
        crop img if handbag detection is successfult, None if no handbag has been detected.
    """
    image = Image.open(io.BytesIO(image_bytes))
    width, hight = image.size
    image_np = np.array(image)
    output_dict = run_inference_for_single_image(model, image_np)
    if 31 in output_dict["detection_classes"]:
        max_score =  max([output_dict["detection_scores"][idx] for idx, i in enumerate(output_dict["detection_classes"]) if i == 31])
        index = list(output_dict["detection_scores"]).index(max_score) 
        [y,x,h,w] = output_dict["detection_boxes"][index]
        cropped = image_np[int(y*hight):int(h*hight), int(x*width):int(w*width)]
        img = Image.fromarray(cropped,"RGB")
        return img
    else:
        return False

def transform_image(image):
    """
    Transform image to get the input for identification model
    
    Parameters
    ----------
    image : image object
        input image
    
    Returns
    -------
    transformed image
    """
    my_transforms = transforms.Compose([transforms.Resize((256,256)),
        transforms.ToTensor(),
        transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225])])
    return my_transforms(image).unsqueeze(0)

def get_prediction(image, model, model_vector):
    """
    Get identification results
    
    Parameters
    ----------
    image : image object
        transformed image
    model : object
        identification model
    model_vector : object
        model used to get handbag vector
    
    Returns
    -------
    prediction results
    """
    tensor = transform_image(image=image)
    outputs = model(tensor)
    vectors = model_vector(tensor).detach().numpy()
    vectors = np.reshape(vectors, [1,2048])
    _, y_hat = outputs.max(1)
    predicted_idx = y_hat.item()

    return brands[predicted_idx], names[predicted_idx], websites[predicted_idx], vectors


# Load identification model 
model, model_vector = load_model()
# Load detection model
# faster rcnn trained using coco 
model_name = 'faster_rcnn_inception_v2_coco_2018_01_28'
detection_model = load_detection_model(model_name)

# Handbag recommendatation 
# read handbag information data
shop_data_shein = pd.read_csv("../data/shop/file/shop_shein_index_final.csv")
shop_data_saks = pd.read_csv("../data/shop/file/shop_bag_index_final.csv")
vectors_shop = torch.load("../data/shop/file/shop_bag_vect_reorder.pt")
vectors_shein = torch.load("../data/shop/file/shop_shein_bag_vect_reorder.pt")
vectors_shop = np.reshape(vectors_shop,[2344,2048])
vectors_shein = np.reshape(vectors_shein,[1270,2048])

def recommend_bag_sim(input_vectors, vectors_bags):
    """
    Calculate similarity of handbags and rank handbags
    
    Parameters
    ----------
    input_vectors : numpy array
        input handbag vector
    vectors_bags : numpy array
        retailer handbags vector
    
    Returns
    -------
    sim_data
        data frame with data ranked by similarity
    """
    sim = cosine_similarity(input_vectors, vectors_bags)
    sim = np.reshape(sim, np.shape(sim)[1])
    sim_dict = [["sim_" + str(i),j] for (i,j) in zip(range(len(sim)), sim)]
    
    sim_data = pd.DataFrame(sim_dict)
    sim_data[2] = [ i.split("_")[0] for i in list(sim_data[0])]
    sim_data["order"] = sim_data[1].rank(ascending=False)
    sim_data.columns = ["initial_index", "sim", "index", "order"]
    return sim_data

def recommend_bag(input_vector, shop_data_saks, shop_data_shein, price_range, vetors_shein, vectors_saks, company):
    """
    Recommend Top3 handbags based criteria
    
    Parameters
    ----------
    input_vector : numpy array
        input handbag vector
    shop_data_saks : data frame
        saks data set
    shop_data_shein : data frame
        shein data set
    price_range : str
        price range required by user
    vetors_shein : numpy array
        handbag vectors from shein
    vectors_shop : numpy array
        handbag vectors from saks
    company : str
        retailer required by user
    
    Returns
    -------
    Top3 recommended handbags
    """
    price_dict = {"less 50": ["less 50"],
                 "less 250" : ["less 50", "less 250"],
                 "less 500" : ["less 50", "less 250", "less 500"],
                 "less 750" : ["less 50", "less 250", "less 500", "less 750"],
                 "less 1000" : ["less 50", "less 250", "less 500", "less 750", "less 1000"],
                 "all" : ["less 50", "less 250", "less 500", "less 750", "less 1000", "higher 1000"]}
    if price_range == "less 50" or company == "shein":
        sim_data = recommend_bag_sim(input_vector, vectors_shein)
        shop_data = pd.merge(shop_data_shein, sim_data, on = shop_data_shein.index)
        shop_data.drop("key_0", axis=1, inplace=True)
        shop_data = shop_data[shop_data["price_level"].isin(price_dict[price_range])].sort_values("sim", ascending=False)
        #Top5 = shop_data[0:5]["image"].values
    
    elif company == "saks":
        sim_data = recommend_bag_sim(input_vector, vectors_shop)
        shop_data = pd.merge(shop_data_saks, sim_data, on = shop_data_saks.index)
        shop_data.drop("key_0", axis=1, inplace=True)
        shop_data = shop_data[shop_data["price_level"].isin(price_dict[price_range])].sort_values("sim", ascending=False)
        #Top5 = shop_data[0:5]["image"].values
    
    else:
        sim_data_saks = recommend_bag_sim(input_vector, vectors_shop)
        shop_data_saks = pd.merge(shop_data_saks, sim_data_saks, on = shop_data_saks.index)
        shop_data_saks.drop("key_0", axis=1, inplace=True)
        
        sim_data_shein = recommend_bag_sim(input_vector, vectors_shein)
        shop_data_shein = pd.merge(shop_data_shein, sim_data_shein, on = shop_data_shein.index)
        shop_data_shein.drop("key_0", axis=1, inplace=True)
        
        shop_data = pd.concat([shop_data_saks, shop_data_shein], axis=0)
        shop_data = shop_data[shop_data["price_level"].isin(price_dict[price_range])].sort_values("sim", ascending=False)
        #Top5 = shop_data[0:5]["image"].values
    Top_1 = get_infor(shop_data, 0)
    Top_2 = get_infor(shop_data, 1)
    Top_3 = get_infor(shop_data, 2)
    return Top_1, Top_2, Top_3

def get_infor(shop_data, index):
    """
    Get the information of data
    
    Parameters
    ----------
    shop_data : data frame
        handbag data set
    index : int
        index of rows
    
    Returns
    -------
    handbag information
    """
    shop_data = shop_data[index:index+1]
    return [shop_data["page"].values[0], shop_data["product"].values[0], shop_data["brand"].values[0], shop_data["price"].values[0], shop_data["image"].values[0], round(shop_data["sim"].values[0],2)]

# Build web app 
@app.route("/")
def hello():
    return render_template('home.html')

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


@app.route('/uploads/<filename>', methods=['GET', 'POST'])
def uploaded_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

@app.route('/predict', methods=['GET', 'POST'])
def predict():
    if request.method == 'POST':
        if "file" not in request.files:
            return redirect(request.url)
        test = request.form["test"]
        if test == "test":
            file = open(os.path.join(app.config['UPLOAD_FOLDER'], "test.jpg"), 'rb')
            filename = "test.jpg"
        else:
            file = request.files['file']
            if not file:
                return render_template("no_result.html")

            if file and allowed_file(file.filename):
                filename = secure_filename(file.filename)
                savename = os.path.join(app.config["UPLOAD_FOLDER"], filename)
                if os.path.exists(savename):
                    os.remove(savename)
                file.save(savename)
                file.stream.seek(0)
                
        img_bytes = file.read()
        option = request.form.get('options')
        if option == "yes":
            infile = crop_image(img_bytes, detection_model)
            if infile == False:
                return render_template("no_result.html")
            savename_crop = os.path.join(app.config["UPLOAD_FOLDER"], filename.split(".")[0] + "_crop.jpg")
            if os.path.exists(savename_crop):
                os.remove(savename_crop)
            infile.save(os.path.join(app.config["UPLOAD_FOLDER"], filename.split(".")[0] + "_crop.jpg"))
            crop_filename = filename.split(".")[0] + "_crop.jpg"
        else:
            infile = Image.open(io.BytesIO(img_bytes))
            crop_filename = filename
        
        brand, name, web, input_vector = get_prediction(image=infile, model=model, model_vector=model_vector)
        if name != "None":
            price_range = request.form.get("price")
            company = request.form.get("company")
            Top1, Top2, Top3 = recommend_bag(input_vector, shop_data_saks, shop_data_shein, price_range, vectors_shein, vectors_shop, company)
            return render_template('result.html', class_brand=brand, class_name=name, class_web=web,
            Top1_product=Top1[1], Top2_product=Top2[1], Top3_product=Top3[1],
            Top1_sim=Top1[-1], Top2_sim=Top2[-1], Top3_sim=Top3[-1],
            Top1_page=Top1[0], Top2_page=Top2[0], Top3_page=Top3[0],
            Top1_brand=Top1[2], Top2_brand=Top2[2], Top3_brand=Top3[2],
            Top1_price=Top1[3], Top2_price=Top2[3], Top3_price=Top3[3],
            Top1_img=Top1[4],Top2_img=Top2[4], Top3_img=Top3[4],
            filename=filename,
            crop_filename=crop_filename)
        else:
            return render_template("no_result.html")
    return render_template('index.html')

if __name__ == '__main__':
    print("Loading PyTorch model and Flask starting server ...")
    print("Please wait until server has fully started")
    app.run(host="0.0.0.0", port=80, debug=False)
    ### Command to run this: FLASK_ENV=development FLASK_APP=app.py FLASK_DEBUG=0 flask run
